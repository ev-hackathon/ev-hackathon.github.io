"""
GitHub Pages용 정적 사이트 만들기: (수집) → 기록 → _site/ 에 사이트와 데이터 JSON을 씀.

GitHub Actions(.github/workflows/deploy.yml)가 15분마다 실행합니다. 서버 없이도 사이트가
실제 데이터를 보여주도록, 서버의 /api/history 응답과 같은 내용을 미리 파일로 만들어 둡니다.

  _site/index.html                 사이트 (docs/index.html)
  _site/data/status.json           마지막 수집 시각, 수집 성공 여부, 기록이 있는 시·군
  _site/data/history/{zscode}.json 오늘(없으면 어제) 시간대별 상태 + 충전소 정보 + 지난 7일 평균
  _site/data/sessions/{zscode}.csv 최근 30일 끝난 충전 세션 (history 브랜치 sessions/ 파일들을 이어 붙인 것)
  _site/data/sessions/index.json   세션 파일 목록·행 수·기록 커밋

사용법:
    python -m scripts.publish_static --history hist/history --out _site            # 수집 + 빌드
    python -m scripts.publish_static --history hist/history --out _site --no-collect # 빌드만
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
from scripts import build_web, collect
from utils import api, history
from utils.logic import KST


def build_site(history_dir: Path, out: Path, *, collected: dict | None, error: str | None, now: datetime) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(build_web.build(), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    data_dir = out / "data" / "history"
    data_dir.mkdir(parents=True, exist_ok=True)

    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    regions = {}
    for name, r in config.REGIONS.items():
        payload = history.day_payload(name, today, history_dir)
        if not payload["hours"]:                      # 자정 직후처럼 오늘 기록이 아직 없으면 어제 것
            prev = history.day_payload(name, yesterday, history_dir)
            if prev["hours"]:
                payload = prev
        if not payload["hours"]:
            continue
        last_h = max(payload["hours"], key=int)
        regions[r["code"]] = {"date": payload["date"], "t": payload["hours"][last_h]["t"],
                              "stations": len(payload["hours"][last_h]["s"])}
        (data_dir / f"{r['code']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    status = {"ok": bool(regions), "mode": "static", "built_at": now.strftime("%Y-%m-%d %H:%M"),
              "collected": collected is not None, "collect_error": error, "regions": regions,
              "refresh_sec": 300}
    (out / "data" / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return status


SESSION_DAYS = 30


def export_sessions(history_dir: Path, out: Path, now: datetime, days: int = SESSION_DAYS) -> dict:
    """끝난 충전 세션을 시·군별 CSV 하나로 (최근 days일, 끝난 날짜 기준).
    GitHub API(로그인 없이 IP당 시간 60회 제한)를 거치지 않고 사이트 주소 하나로 받을 수 있게 하려는 것.
    열은 history 브랜치 sessions/{날짜}/{zscode}.csv와 같다: statId,chgerId,speed,kw,start,end,minutes"""
    src, dst = Path(history_dir) / "sessions", out / "data" / "sessions"
    dst.mkdir(parents=True, exist_ok=True)
    first = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    last = now.strftime("%Y-%m-%d")
    days_dirs = sorted(d for d in (src.iterdir() if src.exists() else []) if d.is_dir() and first <= d.name <= last)
    try:
        commit = subprocess.run(["git", "-C", str(history_dir), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    index = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "window_days": days, "first_day": first, "last_day": last,
             "history_commit": commit, "columns": ["statId", "chgerId", "speed", "kw", "start", "end", "minutes"],
             "note": "끝난 날짜(end) 기준 최근 30일. 15분 간격으로 빠짐없이 기록하기 시작한 때는 2026-11-04 (그 전은 충전기마다 마지막 1건)",
             "regions": {}}
    for r in config.REGIONS.values():
        code, header, rows, files = r["code"], None, [], []
        for d in days_dirs:
            f = d / f"{code}.csv"
            if not f.exists():
                continue
            lines = f.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            header = header or lines[0]
            rows += [x for x in lines[1:] if x.strip()]
            files.append(f"history/sessions/{d.name}/{code}.csv")
        if header is None:
            continue
        (dst / f"{code}.csv").write_text(header + "\n" + "".join(x + "\n" for x in rows), encoding="utf-8")
        index["regions"][code] = {"rows": len(rows), "files": files}
    (dst / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GitHub Pages용 정적 사이트 빌드")
    ap.add_argument("--history", default=str(config.HISTORY_DIR), help="기록 폴더")
    ap.add_argument("--out", default="_site", help="출력 폴더")
    ap.add_argument("--no-collect", action="store_true", help="수집 없이 기존 기록으로만 빌드")
    args = ap.parse_args(argv)

    history_dir, out = Path(args.history), Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    collected, error = None, None
    if not args.no_collect:
        if not api.get_api_key():
            error = "EV_API_KEY가 설정되지 않았습니다 (저장소 Settings > Secrets > Actions)."
        else:
            try:
                collected, _, when = collect.collect_once(history_dir)
                print(f"✔ {when:%Y-%m-%d %H:%M} 수집: " + ", ".join(f"{k} {v}" for k, v in collected.items()))
            except api.ApiError as e:
                error = str(e)
        if error:
            print(f"✘ 수집 실패: {error}")

    now = datetime.now(KST)
    status = build_site(history_dir, out, collected=collected, error=error, now=now)
    print(f"✔ {out}/ 빌드: 기록이 있는 시·군 {len(status['regions'])}곳")
    try:   # 세션 파일 내보내기가 실패해도 사이트 배포는 계속
        idx = export_sessions(history_dir, out, now)
        print(f"✔ {out}/data/sessions/: " + ", ".join(f"{k} {v['rows']:,}건" for k, v in idx["regions"].items()))
    except Exception as e:  # noqa: BLE001
        print(f"⚠ 세션 파일 내보내기 실패: {e}")
    # 수집이 실패해도 사이트는 이전 기록(없으면 가상 데이터)으로 배포합니다.
    # 실패 표시는 워크플로 마지막 단계가 status.json의 collect_error를 보고 합니다.
    if error and os.getenv("GITHUB_ACTIONS"):
        print(f"::warning::{error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
