"""
충전 1건당 충전량(kWh) 요약 → data/energy_fast_43.json

입력은 scripts/fetch_energy.py가 받은 원자료 (Actions '충전량 자료 받기' 결과물):
  public_fast_monthly_43.json       환경부 공공급속 충전기 월별 충전실적 (충북, 실측: 충전기·월별 누적 충전량·횟수)
  synthetic_sessions_43_sample.json 충전건별 충전량 합성데이터 표본 (충북, 가상 자료 — 교차 확인용)

    python -m scripts.energy_summary --raw <원자료 폴더>

1건당 충전량 = 누적 충전량 ÷ 충전 횟수 (최근 12개월). 응답의 누적 충전시간(adupChgtm)은 단위가 맞지 않아
(50kW 충전기 평균 출력이 70kW로 나옴) 쓰지 않는다. 합성데이터의 시각·시간 필드도 충전량과 상관이 없어(−0.02) 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import config

OUT = config.DATA_DIR / "energy_fast_43.json"


def summarize(raw: Path, months: int = 12) -> dict:
    m = pd.DataFrame(json.loads((raw / "public_fast_monthly_43.json").read_text(encoding="utf-8")))
    for c in ("chrgrCpct", "adupChgcpcy", "adupChgfrq"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m["ym"] = pd.to_datetime(m["rlvtYr"] + "-" + m["prfmncMm"] + "-01")
    end = m["ym"].max()
    r = m[(m["ym"] > end - pd.DateOffset(months=months)) & (m["adupChgfrq"] > 0)].copy()
    per = lambda g: round(float(g["adupChgcpcy"].sum() / g["adupChgfrq"].sum()), 2)
    r["per"] = r["adupChgcpcy"] / r["adupChgfrq"]
    w = np.repeat(r["per"].to_numpy(), r["adupChgfrq"].astype(int).to_numpy())       # 충전 건수 가중 분포
    out = {
        "source": "한국환경공단 기후에너지환경부 공공급속 충전기 충전정보 조회 서비스 (getYrMnChgcpcyInfo), 충청북도",
        "period": [f"{r['ym'].min():%Y-%m}", f"{end:%Y-%m}"],
        "method": "1건당 충전량 = 누적 충전량(kWh) ÷ 충전 횟수, 최근 12개월. 환경부 공공급속 충전기만 (민간 운영사·완속 미포함)",
        "fast": {"kwh_per_session": per(r), "sessions": int(r["adupChgfrq"].sum()), "chargers": int(len(r[["chgstnId", "chrgrId"]].drop_duplicates())),
                 "stations": int(r["chgstnId"].nunique()),
                 "quantiles": {f"p{q}": round(float(np.quantile(w, q / 100)), 1) for q in (10, 25, 50, 75, 90)}},
        "by_capacity_kw": {str(int(k)): {"kwh_per_session": per(g), "sessions": int(g["adupChgfrq"].sum())}
                           for k, g in r.groupby("chrgrCpct")},
        "monthly": {f"{k:%Y-%m}": per(g) for k, g in r.groupby("ym")},
        "stations": {sid: {"kwh_per_session": per(g), "sessions": int(g["adupChgfrq"].sum()), "capacity_kw": float(g["chrgrCpct"].max())}
                     for sid, g in r.groupby("chgstnId")},
    }
    syn = raw / "synthetic_sessions_43_sample.json"
    if syn.exists():
        s = pd.DataFrame(json.loads(syn.read_text(encoding="utf-8")))
        s["q"] = pd.to_numeric(s["totChargRcngQnt"], errors="coerce")
        sp = np.where(s["chargCpctSeNm"].str.startswith("급속"), "fast", np.where(s["chargCpctSeNm"].str.startswith("완속"), "slow", ""))
        out["synthetic_check"] = {
            "note": "충전건별 충전량 합성데이터(2024년 환경부 실자료를 본뜬 가상 자료) 충북 무작위 표본. 교차 확인·완속 참고용",
            **{k: {"kwh_mean": round(float(s.loc[sp == k, "q"].mean()), 2), "kwh_median": round(float(s.loc[sp == k, "q"].median()), 2),
                   "n": int((sp == k).sum())} for k in ("fast", "slow")}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    args = ap.parse_args(argv)
    out = summarize(args.raw)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"✔ {OUT.relative_to(config.ROOT)}: 급속 1건당 {out['fast']['kwh_per_session']}kWh "
          f"({out['fast']['sessions']:,}건, {out['period'][0]}~{out['period'][1]}), 충전소 {len(out['stations'])}곳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
