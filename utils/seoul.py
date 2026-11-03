"""
서울 사전학습 모델 (조원 UP024365의 training/train_model.py 방식을 이 저장소에 옮긴 것)

서울시 소유 전기차 충전기 2025년 1년치 충전 이력(세션 시작·종료 시각)을 충전소 × 1시간 격자로 펼쳐
'그 시간에 평균 몇 대가 충전 중이었나 ÷ 동시 사용 최대치' = 이용률을 만들고, 정확히 1시간 뒤 이용률을 학습한다.
충북 기록은 3주치뿐이지만 서울은 1년치 1시간 간격이라 '1시간 뒤' 패턴을 넉넉히 배울 수 있다.

충북 모델(utils/forecast.py)은 이 모델의 예측값(seoul_1h)을 입력 변수 하나로 받는다 (사전학습 + 충북 보정).
학습된 모델은 models/seoul_lgbm.txt (LightGBM 텍스트 형식)로 저장해 Actions에서 다시 학습하지 않는다.

학습: python -m scripts.train_seoul --excel <서울 엑셀 경로>
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import config

MODEL_PATH = config.ROOT / "models" / "seoul_lgbm.txt"
LAGS = (1, 2, 24, 168)
FEATURES = ["hour", "hour_sin", "hour_cos", "day_of_week", "is_weekend", "is_rush_hour",
            "util_now", *[f"util_lag_{h}" for h in LAGS], "util_mean_3h"]
TARGET = "target_util_1h"
LAG_DROP_RATE = 0.4      # 학습 때 지난 값 일부를 비워, 기록이 띄엄띄엄한 충북에서도 쓸 수 있게
SEED = 42


def time_features(ts: pd.Series) -> pd.DataFrame:
    hour, dow = ts.dt.hour, ts.dt.dayofweek
    return pd.DataFrame({"hour": hour, "hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24),
                         "day_of_week": dow, "is_weekend": (dow >= 5).astype(int),
                         "is_rush_hour": hour.isin([8, 9, 18, 19]).astype(int)}, index=ts.index)


def add_mean_3h(df: pd.DataFrame) -> pd.DataFrame:
    df["util_mean_3h"] = df[["util_now", "util_lag_1", "util_lag_2"]].mean(axis=1, skipna=True)
    return df


# ── 서울 학습 데이터 ─────────────────────────────────────────
def load_sessions(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, skiprows=3)
    df.columns = ["station", "start", "end", "kwh"]
    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df = df.dropna(subset=["station", "start", "end"])
    dur = (df["end"] - df["start"]).dt.total_seconds() / 3600
    return df[(dur > 0) & (dur <= 24)].reset_index(drop=True)


def hourly(sessions: pd.DataFrame, year: int = 2025) -> pd.DataFrame:
    """세션 → 충전소 × 1시간 이용률 (충전이 없던 시간도 0으로 채워 shift가 정확히 1시간)"""
    s = sessions[["station", "start", "end"]].copy()
    s["h0"] = s["start"].dt.floor("h")
    s["n"] = np.ceil((s["end"] - s["h0"]) / pd.Timedelta(hours=1)).astype(int)
    rep = s.loc[s.index.repeat(s["n"])].copy()
    rep["slot"] = rep["h0"] + pd.to_timedelta(rep.groupby(level=0).cumcount(), unit="h")
    slot_end = rep["slot"] + pd.Timedelta(hours=1)
    overlap = (np.minimum(rep["end"], slot_end) - np.maximum(rep["start"], rep["slot"])).dt.total_seconds()
    rep["busy"] = overlap.clip(lower=0) / 3600
    busy = rep.groupby(["station", "slot"])["busy"].sum()
    hours = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
    grid = pd.MultiIndex.from_product([busy.index.levels[0], hours], names=["station", "slot"])
    df = busy.reindex(grid, fill_value=0.0).reset_index()
    cap = np.ceil(df.groupby("station")["busy"].transform("max")).clip(lower=1)   # 동시 사용 최대치 ≈ 충전기 수
    df["util_now"] = (df["busy"] / cap).clip(0, 1)
    return df[["station", "slot", "util_now"]]


def features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["station", "slot"]).reset_index(drop=True)
    g = df.groupby("station")["util_now"]
    for h in LAGS:
        df[f"util_lag_{h}"] = g.shift(h)
    df[TARGET] = g.shift(-1)
    df = add_mean_3h(df)
    df = pd.concat([df, time_features(df["slot"])], axis=1)
    return df.dropna(subset=[TARGET])


def drop_some_lags(df: pd.DataFrame, rate: float = LAG_DROP_RATE, seed: int = SEED) -> pd.DataFrame:
    df = df.copy()
    rng = np.random.default_rng(seed)
    for h in LAGS:
        df.loc[rng.random(len(df)) < rate, f"util_lag_{h}"] = np.nan
    return add_mean_3h(df)


# ── 충북에 적용 ──────────────────────────────────────────────
def lag_features(obs: pd.DataFrame) -> pd.DataFrame:
    """충북 관측(statId, fast, ts, u)마다 1·2·24·168시간 전 이용률 (가장 가까운 기록, 없으면 빈 값)"""
    base = obs[["statId", "fast", "ts", "u"]].dropna(subset=["u"]).copy()
    base["sid"] = base["statId"] + ":" + base["fast"].astype(str)
    base["_i"] = np.arange(len(base))
    right = base[["sid", "ts", "u"]]
    out = base[["_i"]].copy()
    for h in LAGS:
        tol = pd.Timedelta(minutes=20 if h < 24 else 45)
        left = base[["sid", "ts", "_i"]].assign(key=base["ts"] - pd.Timedelta(hours=h)).sort_values("key")
        m = pd.merge_asof(left, right.rename(columns={"ts": "key", "u": f"util_lag_{h}"}).sort_values("key"),
                          on="key", by="sid", direction="nearest", tolerance=tol)
        out = out.merge(m[["_i", f"util_lag_{h}"]], on="_i", how="left")
    out = out.sort_values("_i")
    res = base[["statId", "fast", "ts"]].copy()
    for h in LAGS:
        res[f"util_lag_{h}"] = out[f"util_lag_{h}"].to_numpy()
    return res


def load_model():
    import lightgbm as lgb
    return lgb.Booster(model_file=str(MODEL_PATH)) if MODEL_PATH.exists() else None


def predict_1h(booster, now: pd.DataFrame) -> np.ndarray:
    """now: ts, u(현재 이용률), util_lag_* → 서울 모델의 1시간 뒤 이용률"""
    X = pd.concat([now[["u"] + [f"util_lag_{h}" for h in LAGS]].rename(columns={"u": "util_now"}).reset_index(drop=True),
                   time_features(now["ts"].reset_index(drop=True))], axis=1)
    X = add_mean_3h(X)
    return np.clip(booster.predict(X[FEATURES]), 0, 1)
