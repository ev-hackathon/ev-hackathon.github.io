"""
충전 세션 기록: 공공 API의 충전기별 '마지막 충전 시작·종료 시각(lastTsdt, lastTedt)'과
'현재 충전 시작 시각(nowTsdt)'으로 끝난 충전을 한 건씩 쌓는다.

상태 스냅샷(대기/충전 중)만으로는 알 수 없던 것을 추정할 수 있게 된다.
  - 충전 시작 건수 (시간대·충전소별 신규 충전 수요)
  - 충전 시간(점유 시간) 분포
충전량(kWh)·종료 예정 시각·대기 차량 수는 API에 없으므로 여전히 알 수 없다.

저장 형식 (기록 폴더 아래, 시각은 한국 시간)
  sessions/{YYYY-MM-DD}/{zscode}.csv   statId,chgerId,speed,kw,start,end,minutes
      끝난 날짜(end) 기준. 같은 충전기·같은 시작 시각은 한 번만 기록한다.
  sessions/last/{zscode}.json          충전기별 마지막으로 기록한 (시작, 종료) — 중복 방지용
수집 간격(15분) 안에 한 충전기에서 충전이 두 번 끝나면 앞의 것은 놓친다(드묾).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from utils import logic

FIELDS = ("lastTsdt", "lastTedt", "nowTsdt")
MAX_MINUTES = 24 * 60


def _ts(v) -> datetime | None:
    s = str(v or "").strip()
    if len(s) < 12 or not s[:12].isdigit():
        return None
    try:
        return datetime.strptime(s[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def field_coverage(rows: list[dict]) -> dict[str, float]:
    """응답에 세션 필드가 얼마나 들어 있는지 (확인용)"""
    n = max(len(rows), 1)
    return {f: round(sum(1 for r in rows if _ts(r.get(f))) / n, 3) for f in FIELDS}


def append(region: str, rows: list[dict], base: Path | str | None = None) -> int:
    """이번 수집 응답에서 새로 끝난 충전을 기록. 기록한 건수를 돌려줌."""
    root = Path(base) if base else config.HISTORY_DIR
    code = config.REGIONS[region]["code"]
    last_path = root / "sessions" / "last" / f"{code}.json"
    seen = json.loads(last_path.read_text(encoding="utf-8")) if last_path.exists() else {}
    new = []
    for r in rows:
        if str(r.get("delYn", "")).upper() == "Y":
            continue
        start, end = _ts(r.get("lastTsdt")), _ts(r.get("lastTedt"))
        if not start or not end or end <= start:
            continue
        minutes = (end - start).total_seconds() / 60
        if minutes > MAX_MINUTES:
            continue
        key = f"{r.get('statId', '')}|{r.get('chgerId', '')}"
        stamp = f"{start:%Y%m%d%H%M%S}-{end:%Y%m%d%H%M%S}"
        if seen.get(key) == stamp:
            continue
        seen[key] = stamp
        try:
            kw = float(r.get("output") or 0)
        except ValueError:
            kw = 0.0
        speed = logic.classify_speed(str(r.get("chgerType", "")).zfill(2), kw)
        new.append({"statId": r.get("statId", ""), "chgerId": r.get("chgerId", ""), "speed": speed, "kw": kw,
                    "start": f"{start:%Y-%m-%d %H:%M:%S}", "end": f"{end:%Y-%m-%d %H:%M:%S}", "minutes": round(minutes, 1)})
    if new:
        df = pd.DataFrame(new)
        for day, part in df.groupby(df["end"].str[:10]):
            path = root / "sessions" / day / f"{code}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            part.to_csv(path, mode="a", header=not path.exists(), index=False, encoding="utf-8")
    last_path.parent.mkdir(parents=True, exist_ok=True)
    last_path.write_text(json.dumps(seen, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(new)


def load(base: Path | str | None = None) -> pd.DataFrame:
    """쌓인 세션 전체. 열: region, statId, chgerId, speed, kw, start, end, minutes"""
    root = (Path(base) if base else config.HISTORY_DIR) / "sessions"
    frames = []
    for path in sorted(root.glob("20*/*.csv")):
        df = pd.read_csv(path, dtype={"statId": str, "chgerId": str})
        df["region"] = path.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["region", "statId", "chgerId", "speed", "kw", "start", "end", "minutes"])
    df = pd.concat(frames, ignore_index=True)
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    return df.drop_duplicates(["statId", "chgerId", "start"])
