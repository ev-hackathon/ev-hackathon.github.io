"""
신규 충전 건수 방법(utils/demand.py) 검증 — 서울 2025 충전 이력으로.
11월 마지막 14일로 하루 평균 시작률(평균 충전 중 대수 ÷ 평균 충전 시간)을 만들고, 1~11월 시작 시각 분포를 곱해
12월 한 달 충전소별 1시간 충전 시작 건수를 맞혀 본다. 충북 운영과 같은 구성(최근 14일 + 시간대 모양).

    python -m scripts.eval_starts_seoul --excel <서울 엑셀>     → models/start_method_eval.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from utils import demand, seoul


def evaluate(s: pd.DataFrame, window_start: str = "2025-11-17", test_start: str = "2025-12-01") -> dict:
    t0, t1 = pd.Timestamp(window_start), pd.Timestamp(test_start)
    past = s[s["start"] < t1]
    win = past[past["start"] >= t0]
    test = s[s["start"] >= t1]
    hours_win = (t1 - t0) / pd.Timedelta(hours=1)
    busy = ((win["end"].clip(upper=t1) - win["start"]).dt.total_seconds() / 3600).groupby(win["station"]).sum()
    w_min = ((win["end"] - win["start"]).dt.total_seconds() / 60).groupby(win["station"]).mean()
    rate = (busy / hours_win / (w_min / 60)).rename("rate_h")          # 리틀의 법칙: L̄ ÷ W
    prof = lambda g: (lambda v: v / v.mean())(g["start"].dt.hour.value_counts().reindex(range(24), fill_value=0).to_numpy(float))
    shape = {False: prof(past[past["start"].dt.dayofweek < 5]), True: prof(past[past["start"].dt.dayofweek >= 5])}

    hours = pd.date_range(t1, test["start"].max().floor("h"), freq="h")
    g = pd.MultiIndex.from_product([rate.index, hours], names=["station", "hour"]).to_frame(index=False)
    cnt = test.groupby([test["station"], test["start"].dt.floor("h").rename("hour")]).size().rename("obs").reset_index()
    g = g.merge(cnt, on=["station", "hour"], how="left").fillna({"obs": 0}).merge(rate, left_on="station", right_index=True)
    g["we"], g["h"] = g["hour"].dt.dayofweek >= 5, g["hour"].dt.hour
    g["method"] = g["rate_h"] * [shape[w][h] for w, h in zip(g["we"], g["h"])]
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
    return {"setup": f"평균 충전 중 대수·충전 시간: {window_start}~{test_start} (14일), 시간대 모양: 1~11월, 검증: 12월",
            "stations": int(g["station"].nunique()), "station_hours": int(len(g)), "observed_total": int(g["obs"].sum()),
            "method": score("method"), "method_without_shape": score("flat"), "history_mean_by_hour": score("history_mean"),
            "station_monthly_corr": round(float(daily.corr().iloc[0, 1]), 4),
            "hour_of_day_total_error": round(float((hh["method"] - hh["obs"]).abs().sum() / hh["obs"].sum()), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, required=True)
    args = ap.parse_args(argv)
    res = evaluate(seoul.load_sessions(args.excel))
    path = demand.SHAPE_PATH.with_name("start_method_eval.json")
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
