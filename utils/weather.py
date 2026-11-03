"""
기상 데이터 (AI 수요 예측의 입력 변수)

- 학습: 기상청 지상(종관, ASOS) 시간자료 — 지난 시간별 기온·강수량·습도 실측값. 전날까지만 제공된다.
- 예측: 기상청 단기예보의 초단기예보 — 앞으로 6시간 기온·강수량·습도 예보. 승인 전이거나 실패하면 비워 둔다
  (LightGBM은 빈 값을 그대로 처리한다).

인증키는 충전소 API와 같은 공공데이터포털 키(EV_API_KEY)를 쓴다. 받은 ASOS 자료는 기록 폴더의
weather/ 아래에 관측소·날짜별로 저장해 다시 받지 않는다.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ASOS_URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
FCST_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst"

# 시·군 → 가장 가까운 ASOS 관측소 (청주 131, 충주 127, 제천 221, 보은 226, 추풍령 135)
REGION_STATION = {"43110": 131, "43130": 127, "43150": 221, "43720": 226, "43730": 226, "43740": 135,
                  "43745": 131, "43750": 131, "43760": 131, "43770": 127, "43800": 221}
# 시·군 대표 단기예보 격자 (nx, ny)
REGION_GRID = {"43110": (69, 106), "43130": (76, 114), "43150": (81, 118), "43720": (73, 103), "43730": (71, 99),
               "43740": (74, 97), "43745": (71, 110), "43750": (68, 111), "43760": (74, 111), "43770": (72, 113),
               "43800": (84, 115)}
COLS = ["temp", "rain", "humid"]


def _key() -> str | None:
    return os.environ.get("EV_API_KEY") or None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _fetch_asos(stn: int, d0: date, d1: date, key: str) -> list[dict]:
    rows, page = [], 1
    while True:
        r = requests.get(ASOS_URL, params={"serviceKey": requests.utils.unquote(key), "pageNo": page, "numOfRows": 999,
                                           "dataType": "JSON", "dataCd": "ASOS", "dateCd": "HR",
                                           "startDt": d0.strftime("%Y%m%d"), "startHh": "00",
                                           "endDt": d1.strftime("%Y%m%d"), "endHh": "23", "stnIds": stn}, timeout=30)
        body = r.json()["response"]["body"]
        items = body["items"]["item"] if body.get("items") else []
        rows += items
        if page * 999 >= int(body.get("totalCount", 0)):
            return rows
        page += 1


def load_asos(history_dir: str, start: datetime, end: datetime) -> pd.DataFrame:
    """관측소·시각별 기온(℃)·강수량(mm)·습도(%). 열: stn, ts, temp, rain, humid. 저장본이 없으면 받아서 저장."""
    root = Path(history_dir) / "weather"
    root.mkdir(parents=True, exist_ok=True)
    last_ok = date.today() - timedelta(days=1)          # ASOS는 전날까지만 제공
    d_start, d_end = start.date(), min(end.date(), last_ok)
    key, frames = _key(), []
    for stn in sorted(set(REGION_STATION.values())):
        days = [d_start + timedelta(days=i) for i in range((d_end - d_start).days + 1)]
        missing = [d for d in days if not (root / f"asos_{stn}_{d:%Y%m%d}.json").exists()]
        if missing and key:
            try:
                got = _fetch_asos(stn, missing[0], missing[-1], key)
                by_day: dict[str, list] = {}
                for it in got:
                    by_day.setdefault(it["tm"][:10].replace("-", ""), []).append(
                        {"tm": it["tm"], "ta": it.get("ta"), "rn": it.get("rn"), "hm": it.get("hm")})
                for d in missing:
                    k = d.strftime("%Y%m%d")
                    if k in by_day and len(by_day[k]) >= 20:    # 하루치가 거의 다 있을 때만 저장
                        (root / f"asos_{stn}_{k}.json").write_text(json.dumps(by_day[k], ensure_ascii=False), encoding="utf-8")
            except Exception as e:                            # 날씨 실패가 예측 전체를 막지 않게
                print(f"⚠ ASOS {stn} 받기 실패: {e}")
        for d in days:
            p = root / f"asos_{stn}_{d:%Y%m%d}.json"
            if p.exists():
                for it in json.loads(p.read_text(encoding="utf-8")):
                    frames.append({"stn": stn, "ts": pd.Timestamp(it["tm"]), "temp": _num(it["ta"]),
                                   "rain": _num(it["rn"]) if it["rn"] not in ("", None) else 0.0, "humid": _num(it["hm"])})
    return pd.DataFrame(frames, columns=["stn", "ts", *COLS])


def forecast_now(issued: datetime, hours: int = 6) -> pd.DataFrame:
    """초단기예보: 시·군별 앞으로 몇 시간 기온·강수·습도. 열: region, ts, temp, rain, humid (실패하면 빈 표)"""
    key = _key()
    if not key:
        return pd.DataFrame(columns=["region", "ts", *COLS])
    base = issued - timedelta(minutes=45)                # 매시 30분 발표, 45분 이후 제공
    bd, bt = base.strftime("%Y%m%d"), base.strftime("%H") + "30"
    out = []
    for region, (nx, ny) in REGION_GRID.items():
        try:
            r = requests.get(FCST_URL, params={"serviceKey": requests.utils.unquote(key), "pageNo": 1, "numOfRows": 200,
                                               "dataType": "JSON", "base_date": bd, "base_time": bt, "nx": nx, "ny": ny}, timeout=20)
            items = r.json()["response"]["body"]["items"]["item"]
        except Exception:
            continue
        cell: dict[pd.Timestamp, dict] = {}
        for it in items:
            ts = pd.Timestamp(f"{it['fcstDate']} {it['fcstTime'][:2]}:00")
            v = it["fcstValue"]
            c = cell.setdefault(ts, {})
            if it["category"] == "T1H":
                c["temp"] = _num(v)
            elif it["category"] == "RN1":
                c["rain"] = 0.0 if "없음" in str(v) else _num(str(v).replace("mm", "").split("~")[0].replace("미만", ""))
            elif it["category"] == "REH":
                c["humid"] = _num(v)
        out += [{"region": region, "ts": ts, **c} for ts, c in cell.items()]
    return pd.DataFrame(out, columns=["region", "ts", *COLS])


def attach(df: pd.DataFrame, asos: pd.DataFrame, when_col: str = "ts_t", fcst: pd.DataFrame | None = None) -> pd.DataFrame:
    """df의 시·군과 예측 대상 시각(정시로 내림)에 날씨 열(temp, rain, humid)을 붙인다."""
    df = df.copy()
    hour = df[when_col].dt.floor("h")
    key = pd.DataFrame({"region": df["region"].astype(str), "hour": hour})
    key["stn"] = key["region"].map(REGION_STATION)
    w = asos.rename(columns={"ts": "hour"}).astype({"stn": float})
    key["stn"] = key["stn"].astype(float)
    m = key.merge(w, on=["stn", "hour"], how="left")
    if fcst is not None and len(fcst):
        f = fcst.rename(columns={"ts": "hour"}).assign(region=lambda d: d["region"].astype(str))
        m2 = key.merge(f, on=["region", "hour"], how="left")
        for c in COLS:
            m[c] = m[c].fillna(m2[c])
    for c in COLS:
        df[c] = pd.to_numeric(m[c], errors="coerce").astype(float).to_numpy()
    return df
