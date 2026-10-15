"""
한국환경공단 전기자동차 충전소 정보 API (getChargerInfo) 클라이언트.

- 응답은 '충전소'가 아니라 '충전기 1대 = 1행'입니다.
- totalCount를 보고 마지막 페이지까지 모두 가져옵니다.
- 실패 시 빈 리스트 대신 ApiError를 던집니다(빈 결과가 캐시되는 문제 방지).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

load_dotenv()


class ApiError(RuntimeError):
    """API 호출 실패 (키 오류, 네트워크, 응답 형식 이상 등)."""


def get_api_key() -> str | None:
    key = os.getenv("EV_API_KEY", "").strip()
    if not key:
        return None
    # 포털의 'Encoding' 키를 넣어도 이중 인코딩되지 않도록 디코딩 키로 통일
    return unquote(key) if "%" in key else key


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _parse(response: requests.Response) -> tuple[list[dict], int]:
    try:
        data = response.json()
    except ValueError:
        # 인증키 오류 등은 dataType=JSON 이어도 XML로 오는 경우가 있음
        snippet = response.text[:200].replace("\n", " ")
        raise ApiError(f"JSON이 아닌 응답을 받았습니다 (인증키 확인 필요): {snippet}")

    code = str(data.get("resultCode", "00"))
    if code not in ("00", "0"):
        raise ApiError(f"API 오류 [{code}] {data.get('resultMsg', '')}")

    items = data.get("items") or {}
    rows = items.get("item", []) if isinstance(items, dict) else items
    if isinstance(rows, dict):  # 결과가 1건이면 dict로 옴
        rows = [rows]
    total = int(data.get("totalCount") or len(rows))
    return list(rows), total


def fetch_page(zscode: str | None, page: int, per_page: int, *, api_key: str,
               session: requests.Session | None = None) -> tuple[list[dict], int]:
    """zscode가 None이면 충북 전체(zcode만)를 조회."""
    params = {
        "serviceKey": api_key,
        "pageNo": page,
        "numOfRows": per_page,
        "dataType": "JSON",
        "zcode": config.SIDO_CODE,
    }
    if zscode:
        params["zscode"] = zscode
    s = session or _session()
    timeout = config.REQUEST_TIMEOUT if per_page <= config.PAGE_SIZE else config.REQUEST_TIMEOUT * 4
    try:
        r = s.get(config.API_URL, params=params, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ApiError(f"네트워크 오류: {e.__class__.__name__}") from e
    return _parse(r)


def fetch_all(zscode: str | None, *, api_key: str, per_page: int = config.PAGE_SIZE,
              session: requests.Session | None = None) -> list[dict]:
    """한 시군구(None이면 충북 전체)의 충전기 행을 페이지를 넘기며 모두 수집."""
    s = session or _session()
    rows: list[dict] = []
    for page in range(1, config.MAX_PAGES + 1):
        batch, total = fetch_page(zscode, page, per_page, api_key=api_key, session=s)
        rows.extend(batch)
        if not batch or len(rows) >= total:
            break
    return rows


def fetch_region(region_name: str, *, api_key: str | None = None) -> list[dict]:
    """지역명(config.REGIONS 키)으로 조회. 대표 코드가 비면 하위 구 코드로 재시도."""
    key = api_key or get_api_key()
    if not key:
        raise ApiError("EV_API_KEY가 설정되지 않았습니다 (.env 확인).")
    region = config.REGIONS[region_name]
    s = _session()
    rows = fetch_all(region["code"], api_key=key, session=s)
    if not rows:
        for sub in region.get("fallback", []):
            rows.extend(fetch_all(sub, api_key=key, session=s))
    return rows


def region_of(row: dict) -> str | None:
    """충전기 행이 어느 시·군 것인지. zscode(청주는 구 코드 4311x 포함)로, 없으면 주소로 판정."""
    z = str(row.get("zscode") or "").strip()
    for name, r in config.REGIONS.items():
        if z and (z == r["code"] or z in r.get("fallback", [])):
            return name
    addr = str(row.get("addr") or "")
    for name in config.REGIONS:
        if name in addr:
            return name
    return None


def fetch_province(*, api_key: str | None = None) -> dict[str, list[dict]]:
    """충북 전체를 큰 페이지로 한 번에 받아 시·군별로 나눔. 기록 수집용 (API 호출 수 절약)."""
    key = api_key or get_api_key()
    if not key:
        raise ApiError("EV_API_KEY가 설정되지 않았습니다 (.env 확인).")
    rows = fetch_all(None, api_key=key, per_page=config.COLLECT_PAGE_SIZE)
    out: dict[str, list[dict]] = {name: [] for name in config.REGIONS}
    for r in rows:
        name = region_of(r)
        if name:
            out[name].append(r)
    return out


# ── 스냅샷 (발표장 네트워크가 불안정할 때를 대비한 오프라인 데이터) ──────────
def snapshot_path(region_name: str) -> Path:
    return config.SNAPSHOT_DIR / f"{config.REGIONS[region_name]['code']}.json"


def save_snapshot(region_name: str, rows: list[dict]) -> Path:
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(region_name)
    payload = {"region": region_name, "saved_at": datetime.now().isoformat(timespec="seconds"), "rows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_snapshot(region_name: str) -> tuple[list[dict], str] | None:
    path = snapshot_path(region_name)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("rows", []), payload.get("saved_at", "")
