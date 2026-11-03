"""
신규 충전 건수 방법(utils/demand.py) 검증 — 서울 2025 충전 이력으로 (정답 시작 시각을 아는 데이터).
충북 운영과 똑같이 만든다:
  하루 평균 시작률 = 11월 마지막 14일 평균 충전 중 대수 ÷ 평균 충전 시간
  시간대 모양     = '충전 중 대수' 스냅숏 160개(2~11월 무작위 시각, 충북 수집처럼 띄엄띄엄)를 역합성곱 — 시작 시각은 안 씀
그리고 12월 한 달 충전소별 1시간 충전 시작 건수를 맞혀 본다.

    python -m scripts.eval_starts_seoul --excel <서울 엑셀>     → models/start_method_eval.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import config
from utils import demand, seoul


def evaluate(s: pd.DataFrame, window_start: str = "2025-11-17", test_start: str = "2025-12-01",
             n_snapshots: int = 160, seed: int = 0) -> dict:
    t0, t1 = pd.Timestamp(window_start), pd.Timestamp(test_start)
    past = s[s["start"] < t1]
    win = past[past["start"] >= t0]
    test = s[s["start"] >= t1]
    hours_win = (t1 - t0) / pd.Timedelta(hours=1)
    busy = ((win["end"].clip(upper=t1) - win["start"]).dt.total_seconds() / 3600).groupby(win["station"]).sum()
    w_min = ((win["end"] - win["start"]).dt.total_seconds() / 60).groupby(win["station"]).mean()
    rate = (busy / hours_win / (w_min / 60)).rename("rate_h")          # 리틀의 법칙: L̄ ÷ W
    # 시간대 모양: 띄엄띄엄한 스냅숏의 충전 중 대수만으로 역합성곱 (충북과 같은 방법)
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-02-01", t1 - pd.Timedelta(days=1), freq="D")
    snaps = pd.to_datetime(rng.choice(days.values, n_snapshots)) + pd.to_timedelta(rng.integers(0, 1440, n_snapshots), unit="m")
    st, en = past["start"].values, past["end"].values
    occ = np.array([((st <= t) & (en > t)).sum() for t in snaps.values], float)
    lam = demand.deconvolve((snaps.hour * 60 + snaps.minute).to_numpy(), occ,
                            ((past["end"] - past["start"]).dt.total_seconds() / 60).to_numpy())
    shape96 = lam / lam.mean()
    true96 = np.bincount((past["start"].dt.hour * 4 + past["start"].dt.minute // 15).to_numpy(), minlength=96).astype(float)
    true96 /= true96.mean()
    hr = lambda v: v.reshape(24, 4).mean(1)
    hours = pd.date_range(t1, test["start"].max().floor("h"), freq="h")
    g = pd.MultiIndex.from_product([rate.index, hours], names=["station", "hour"]).to_frame(index=False)
    cnt = test.groupby([test["station"], test["start"].dt.floor("h").rename("hour")]).size().rename("obs").reset_index()
    g = g.merge(cnt, on=["station", "hour"], how="left").fillna({"obs": 0}).merge(rate, left_on="station", right_index=True)
    g["we"], g["h"] = g["hour"].dt.dayofweek >= 5, g["hour"].dt.hour
    g["method"] = g["rate_h"] * hr(shape96)[g["h"].to_numpy()]
    g["true_shape"] = g["rate_h"] * hr(true96)[g["h"].to_numpy()]
    g["flat"] = g["rate_h"]
    # 비교 기준: 그 충전소·평일/주말·시각의 1~11월 평균 시작 건수 (과거 이력을 그대로 쓰는 방법)
    days = past["start"].dt.normalize().drop_duplicates()
    nd = {False: int((days.dt.dayofweek < 5).sum()), True: int((days.dt.dayofweek >= 5).sum())}
    c = past.groupby([past["station"], (past["start"].dt.dayofweek >= 5).rename("we"), past["start"].dt.hour.rename("h")]).size()
    hist = (c / [nd[w] for w in c.index.get_level_values("we")]).rename("history_mean").reset_index()
    g = g.merge(hist, on=["station", "we", "h"], how="left").fillna({"history_mean": 0})

    def score(p):
        y, m = g["obs"].to_numpy(), np.clip(g[p].to_numpy(), 1e-9, None)
        with np.errstate(divide="ignore", invalid="ignore"):
            dev = 2 * np.mean(np.where(y > 0, y * np.log(y / m), 0) - (y - m))
        return {"poisson_deviance": round(float(dev), 4), "RMSE": round(float(np.sqrt(np.mean((g[p] - g["obs"]) ** 2))), 4),
                "MAE": round(float(np.mean(np.abs(g[p] - g["obs"]))), 4), "total": int(round(g[p].sum()))}
    daily = g.groupby("station")[["obs", "method"]].sum()
    hh = g.groupby("h")[["obs", "method"]].sum()
    return {"setup": f"시작률: {window_start}~{test_start} (14일) 평균 충전 중 대수 ÷ 평균 충전 시간 / "
                     f"시간대 모양: 스냅숏 {n_snapshots}개 역합성곱 / 검증: 12월 충전소별 1시간 시작 건수",
            "shape_recovery": {"corr": round(float(np.corrcoef(hr(shape96), hr(true96))[0, 1]), 4),
                               "MAE": round(float(np.abs(hr(shape96) - hr(true96)).mean()), 4),
                               "recovered": [round(float(v), 2) for v in hr(shape96)],
                               "true": [round(float(v), 2) for v in hr(true96)]},
            "stations": int(g["station"].nunique()), "station_hours": int(len(g)), "observed_total": int(g["obs"].sum()),
            "method": score("method"), "method_with_true_shape": score("true_shape"), "method_without_shape": score("flat"), "history_mean_by_hour": score("history_mean"),
            "station_monthly_corr": round(float(daily.corr().iloc[0, 1]), 4),
            "hour_of_day_total_error": round(float((hh["method"] - hh["obs"]).abs().sum() / hh["obs"].sum()), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, required=True)
    args = ap.parse_args(argv)
    res = evaluate(seoul.load_sessions(args.excel))
    path = config.ROOT / "models" / "start_method_eval.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
