"""
AI 충전 수요 예측: 충전소별 1시간 후 이용률(충전 중 ÷ 정상 충전기)을 LightGBM으로 예측한다.

입력: GitHub Actions가 쌓은 상태 기록(history/ 날짜별 CSV + stations/ 충전소 정보)
출력: data/forecast.json  (시뮬레이션 탭과 관제 화면이 읽는 형식, schema_version 1)

예측 간격을 입력 변수로 넣는 이유
  GitHub 예약 실행이 들쭉날쭉해 수집 간격이 일정하지 않다(중앙값 약 3시간). 정확히 1시간 뒤 짝만으로는
  학습 데이터가 거의 없으므로, 0.5~8시간 뒤의 짝을 모두 쓰고 '몇 시간 뒤인지'를 변수로 넣어 학습한 다음
  예측할 때 1시간을 넣는다. 수집 간격이 촘촘해지면 같은 코드로 1시간 근처 짝이 자연히 늘어난다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config
from utils import history, prices, weather

KST_OFFSET = "+09:00"
MIN_H, MAX_H = 0.5, 8.0          # 학습에 쓰는 예측 간격(시간)
FEATURES = ["u0", "usable0", "fault0", "fast", "price", "horizon", "hour0", "hour_t", "dow_t", "weekend_t",
            "hist_hour", "hist_dow_hour", "hist_mean", "hist_full", "hist_n", "region",
            "prev_u", "prev_gap", "recent24",
            "temp", "rain", "humid"]          # 날씨: 예측 대상 시각의 기온·강수량·습도 (utils/weather.py)
MAX_TRAIN = 700_000              # 학습 짝이 이보다 많으면 무작위 추출 (Actions 실행 시간 제한)
MODEL_NAME = "LightGBM 이용률 예측 v2 (현재값 결합)"
DEFAULT_BLEND = 0.5            # 1시간 예측 = w × 현재 이용률 + (1 − w) × LightGBM. w는 1시간 검증으로 정함


# ─────────────────────────────────────────────────────────────
# 1. 기록 → 관측치 (충전소 × 속도 × 수집 시각)
# ─────────────────────────────────────────────────────────────
def observations(history_dir: str) -> pd.DataFrame:
    """수집 시각마다 전 충전소의 상태를 복원해 긴 표로 만든다. 열: ts, region, statId, fast, a, c, f, u"""
    days = sorted(d for d in os.listdir(history_dir) if d[:2] == "20")
    frames = []
    for name, r in config.REGIONS.items():
        for d in days:
            df = history.load_day(name, d, history_dir)
            if df.empty:
                continue
            runs = sorted(df.loc[df["statId"] == history.RUN_MARK, "t"].unique())
            data = df[df["statId"] != history.RUN_MARK]
            for t in runs:   # t 시각까지 이어 붙인 마지막 상태 = 그 시각 전체 상태
                st = data[data["t"] <= t].groupby("statId", sort=False)[list(history.COUNT_COLS)].last()
                ts = pd.Timestamp(f"{d} {t[:2]}:{t[2:]}")
                for fast, p in ((1, "f"), (0, "s")):
                    sub = st[[p + "a", p + "c", p + "f"]]
                    sub = sub[(sub.sum(axis=1) > 0)]
                    frames.append(pd.DataFrame({"ts": ts, "region": r["code"], "statId": sub.index, "fast": fast,
                                                "a": sub[p + "a"].to_numpy(), "c": sub[p + "c"].to_numpy(),
                                                "f": sub[p + "f"].to_numpy()}))
    obs = pd.concat(frames, ignore_index=True)
    usable = obs["a"] + obs["c"]
    obs["u"] = np.where(usable > 0, obs["c"] / usable.where(usable > 0, 1), np.nan)
    obs["full"] = ((obs["a"] == 0) & (obs["c"] > 0)).astype(int)
    obs = obs.drop_duplicates(["ts", "region", "statId", "fast"]).sort_values("ts").reset_index(drop=True)
    # 최근 흐름: 직전 관측 이용률과 그 간격, 최근 24시간 평균 이용률
    g = obs.groupby(["statId", "fast"])
    obs["prev_u"] = g["u"].shift(1)
    obs["prev_gap"] = (obs["ts"] - g["ts"].shift(1)).dt.total_seconds() / 3600
    r = obs.set_index("ts").groupby(["statId", "fast"])["u"].rolling("24h").mean().rename("recent24").reset_index()
    return obs.merge(r, on=["statId", "fast", "ts"], how="left")


def station_prices(history_dir: str) -> dict:
    """(statId, fast) → 운영사 회원가 (utils/prices.py, 실제 요금표)"""
    out = {}
    for name in config.REGIONS:
        for sid, m in history.load_meta(name, history_dir).items():
            if "fk" in m or "fb" in m:
                out[(sid, 1)] = prices.lookup(m.get("b", ""), m.get("fk", 50.0))[0]
            if "sk" in m or "sb" in m:
                out[(sid, 0)] = prices.lookup(m.get("b", ""), m.get("sk", 7.0))[0]
    return out


# ─────────────────────────────────────────────────────────────
# 2. 과거 패턴 특징 (학습 구간에서만 계산해 미래 정보가 새지 않게)
# ─────────────────────────────────────────────────────────────
@dataclass
class Profile:
    hour: pd.Series        # (statId, fast, hour) → 평균 이용률
    dow_hour: pd.Series    # (statId, fast, weekend, hour)
    mean: pd.Series        # (statId, fast)
    full: pd.Series        # (statId, fast) → 만차 비율
    n: pd.Series           # (statId, fast) → 관측 수
    glob_hour: pd.Series   # (fast, hour) → 전체 평균 (충전소 기록이 없을 때)


def build_profile(obs: pd.DataFrame) -> Profile:
    o = obs.dropna(subset=["u"]).assign(hour=lambda d: d["ts"].dt.hour, weekend=lambda d: (d["ts"].dt.dayofweek >= 5).astype(int))
    return Profile(
        hour=o.groupby(["statId", "fast", "hour"])["u"].mean(),
        dow_hour=o.groupby(["statId", "fast", "weekend", "hour"])["u"].mean(),
        mean=o.groupby(["statId", "fast"])["u"].mean(),
        full=o.groupby(["statId", "fast"])["full"].mean(),
        n=o.groupby(["statId", "fast"])["u"].size(),
        glob_hour=o.groupby(["fast", "hour"])["u"].mean(),
    )


def _lookup(series: pd.Series, keys: list[tuple]) -> np.ndarray:
    return series.reindex(pd.MultiIndex.from_tuples(keys, names=series.index.names)).to_numpy(dtype=float)


def add_features(df: pd.DataFrame, prof: Profile, price_map: dict) -> pd.DataFrame:
    """df: statId, fast, region, ts0, ts_t, u0, usable0, fault0 → 모델 입력 열 추가"""
    df = df.copy()
    df["horizon"] = (df["ts_t"] - df["ts0"]).dt.total_seconds() / 3600
    df["hour0"] = df["ts0"].dt.hour
    df["hour_t"] = df["ts_t"].dt.hour
    df["dow_t"] = df["ts_t"].dt.dayofweek
    df["weekend_t"] = (df["dow_t"] >= 5).astype(int)
    sf = list(zip(df["statId"], df["fast"]))
    df["hist_hour"] = _lookup(prof.hour, [(s, f, h) for (s, f), h in zip(sf, df["hour_t"])])
    df["hist_dow_hour"] = _lookup(prof.dow_hour, [(s, f, w, h) for (s, f), w, h in zip(sf, df["weekend_t"], df["hour_t"])])
    df["hist_mean"] = _lookup(prof.mean, sf)
    df["hist_full"] = _lookup(prof.full, sf)
    df["hist_n"] = _lookup(prof.n, sf)
    glob = _lookup(prof.glob_hour, list(zip(df["fast"], df["hour_t"])))
    df["hist_hour"] = df["hist_hour"].fillna(df["hist_mean"]).fillna(pd.Series(glob, index=df.index))
    df["hist_dow_hour"] = df["hist_dow_hour"].fillna(df["hist_hour"])
    df["hist_mean"] = df["hist_mean"].fillna(df["hist_hour"])
    for c in ("hist_hour", "hist_dow_hour", "hist_mean"):   # 그 시간대 기록이 아예 없으면 속도별 평균
        df[c] = df[c].fillna(df.groupby("fast")[c].transform("mean")).fillna(0)
    df["hist_n"] = df["hist_n"].fillna(0)
    df["price"] = [price_map.get(k, np.nan) for k in sf]
    df["region"] = df["region"].astype(int)
    return df


def prepare(pairs: pd.DataFrame, prof: Profile, price_map: dict, asos: pd.DataFrame | None,
            fcst: pd.DataFrame | None = None) -> pd.DataFrame:
    """특징 + 날씨. 날씨 자료가 없으면 날씨 열은 비워 둔다 (LightGBM이 빈 값을 처리)."""
    df = add_features(pairs, prof, price_map)
    return weather.attach(df, asos if asos is not None else pd.DataFrame(columns=["stn", "ts", *weather.COLS]), fcst=fcst)


# ─────────────────────────────────────────────────────────────
# 3. 학습용 짝: 지금 상태(t0) → h시간 뒤 이용률(t)
# ─────────────────────────────────────────────────────────────
def make_pairs(obs: pd.DataFrame) -> pd.DataFrame:
    o = obs.dropna(subset=["u"]).sort_values("ts")
    runs = o[["region", "ts"]].drop_duplicates()
    # 같은 시·군 안에서 (수집 t0, 수집 t) 쌍 중 간격이 MIN_H~MAX_H인 것
    rp = runs.merge(runs, on="region", suffixes=("0", "_t"))
    h = (rp["ts_t"] - rp["ts0"]).dt.total_seconds() / 3600
    rp = rp[(h >= MIN_H) & (h <= MAX_H)]
    a = o.drop(columns=[]).rename(columns={"ts": "ts0", "u": "u0", "a": "a0", "c": "c0", "f": "fault0", "full": "full0"})
    b = o[["ts", "region", "statId", "fast", "u", "full"]].rename(columns={"ts": "ts_t", "u": "y", "full": "y_full"})
    pairs = rp.merge(a, on=["region", "ts0"]).merge(b, on=["region", "ts_t", "statId", "fast"])
    pairs["usable0"] = pairs["a0"] + pairs["c0"]
    return pairs.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 4. 학습 · 평가
# ─────────────────────────────────────────────────────────────
def _models(algo: str = "lightgbm"):
    if algo == "xgboost":
        import xgboost as xgb
        common = dict(n_estimators=400, learning_rate=0.05, max_depth=6, min_child_weight=50, subsample=0.8,
                      colsample_bytree=0.8, random_state=42, tree_method="hist", n_jobs=-1)
        return xgb.XGBRegressor(objective="reg:squarederror", **common), xgb.XGBClassifier(objective="binary:logistic", **common)
    import lightgbm as lgb
    common = dict(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=50,
                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=42, verbose=-1)
    return lgb.LGBMRegressor(objective="regression", **common), lgb.LGBMClassifier(objective="binary", **common)


def fit(train: pd.DataFrame, algo: str = "lightgbm"):
    if len(train) > MAX_TRAIN:
        train = train.sample(MAX_TRAIN, random_state=0)
    reg, clf = _models(algo)
    X = train[FEATURES]
    if algo == "xgboost":
        reg.fit(X, train["y"]); clf.fit(X, train["y_full"])
    else:
        reg.fit(X, train["y"], categorical_feature=["region"])
        clf.fit(X, train["y_full"], categorical_feature=["region"])
    return reg, clf


def _score(test: pd.DataFrame, reg, clf) -> dict:
    """한 검증 세트에 대한 모델·기준선 성능"""
    from sklearn.metrics import roc_auc_score
    p = np.clip(reg.predict(test[FEATURES]), 0, 1)
    pf = clf.predict_proba(test[FEATURES])[:, 1]
    y, yf = test["y"].to_numpy(), test["y_full"].to_numpy()
    true_st = status_of(y, yf.astype(float))
    return {"n": int(len(test)), "model": _err(y, p),
            "baseline_persistence": _err(y, test["u0"].to_numpy()),
            "baseline_history": _err(y, test["hist_dow_hour"].to_numpy()),
            "status_accuracy": {"model": float(np.mean(status_of(p, pf) == true_st)),
                                "persistence": float(np.mean(status_of(test["u0"].to_numpy(), test["full0"].to_numpy().astype(float)) == true_st))},
            "full_auc": float(roc_auc_score(yf, pf)) if yf.min() != yf.max() else None}


def evaluate_one_hour(obs: pd.DataFrame, price_map: dict, band=(0.75, 1.25), min_pairs: int = 2000, max_days: int = 3,
                      asos: pd.DataFrame | None = None) -> dict:
    """정확히 '1시간 뒤'(45~75분) 성능. 수집이 촘촘했던 날을 하루씩 통째로 빼고 나머지 날로 학습해 그날로 검증한다."""
    pairs_all = make_pairs(obs)
    h = (pairs_all["ts_t"] - pairs_all["ts0"]).dt.total_seconds() / 3600
    near = pairs_all[(h >= band[0]) & (h <= band[1])]
    counts = near.groupby(near["ts0"].dt.normalize()).size()
    days = sorted(counts[counts >= min_pairs].index)[-max_days:]
    out = {"band_hours": list(band), "days": [], "pooled": None, "blend_weight": DEFAULT_BLEND}
    preds = []
    for d in days:
        nd = d + pd.Timedelta(days=1)
        other = obs[(obs["ts"] < d) | (obs["ts"] >= nd)]
        prof = build_profile(other)
        pairs = prepare(pairs_all, prof, price_map, asos)
        touches = ((pairs["ts0"] >= d) & (pairs["ts0"] < nd)) | ((pairs["ts_t"] >= d) & (pairs["ts_t"] < nd))
        test = pairs[(pairs["ts0"] >= d) & (pairs["ts0"] < nd) & pairs["horizon"].between(*band)]
        reg, clf = fit(pairs[~touches])
        r = _score(test, reg, clf); r["day"] = str(d.date())
        out["days"].append(r)
        preds.append(test.assign(p=np.clip(reg.predict(test[FEATURES]), 0, 1), pf=clf.predict_proba(test[FEATURES])[:, 1]))
    if preds:
        allp = pd.concat(preds)
        y, u0, pm = allp["y"].to_numpy(), allp["u0"].to_numpy(), allp["p"].to_numpy()
        # 현재값과 모델의 결합 비율: 1시간 검증에서 RMSE가 가장 낮은 값 (날마다 같은 무게로)
        grid = np.round(np.arange(0, 1.01, 0.1), 1)
        day_w = allp["ts0"].dt.normalize().map(1 / allp["ts0"].dt.normalize().value_counts()).to_numpy()
        wr = lambda w: float(np.sqrt(np.average((y - (w * u0 + (1 - w) * pm)) ** 2, weights=day_w)))
        w = float(min(grid, key=wr))
        bl = w * u0 + (1 - w) * pm
        bf = w * allp["full0"].to_numpy() + (1 - w) * allp["pf"].to_numpy()
        true_st = status_of(y, allp["y_full"].to_numpy().astype(float))
        out["blend_weight"] = w
        out["pooled"] = {"n": int(len(allp)), "model": _err(y, pm), "blend": _err(y, bl),
                         "baseline_persistence": _err(y, u0),
                         "baseline_history": _err(y, allp["hist_dow_hour"].to_numpy()),
                         "status_accuracy": {"blend": float(np.mean(status_of(bl, bf) == true_st)),
                                             "persistence": float(np.mean(status_of(u0, allp["full0"].to_numpy().astype(float)) == true_st))},
                         "rmse_by_weight": {f"{g:.1f}": round(wr(g), 4) for g in grid}}
    return out


def status_of(u: np.ndarray, p_full: np.ndarray, th: float = config.DEFAULT_POLICY.discount_threshold) -> np.ndarray:
    """예측 이용률·만차 확률 → 관제 판정 (여유: 빈 충전기 비율 ≥ 기준, 혼잡: 만차 확률 ≥ 0.5)"""
    return np.where(p_full >= 0.5, "혼잡", np.where(1 - u >= th, "여유", "보통"))


def _err(y, p):
    return {"MAE": float(np.mean(np.abs(y - p))), "RMSE": float(np.sqrt(np.mean((y - p) ** 2)))}


def evaluate(obs: pd.DataFrame, price_map: dict, test_days: int = 5, asos: pd.DataFrame | None = None) -> dict:
    """마지막 test_days일을 떼어 두고 학습 → 기준선과 비교 (기록이 짧으면 전체의 1/4 정도만 떼어 둠)"""
    span = (obs["ts"].max().normalize() - obs["ts"].min().normalize()).days + 1
    test_days = max(1, min(test_days, span // 4))
    cut = obs["ts"].max().normalize() - pd.Timedelta(days=test_days - 1)
    prof = build_profile(obs[obs["ts"] < cut])
    pairs = prepare(make_pairs(obs), prof, price_map, asos)
    train = pairs[(pairs["ts_t"] < cut)]
    test = pairs[(pairs["ts0"] >= cut)]
    reg, clf = fit(train)
    p = np.clip(reg.predict(test[FEATURES]), 0, 1)
    pf = clf.predict_proba(test[FEATURES])[:, 1]
    y = test["y"].to_numpy()
    out = {"train_period": [str(obs["ts"].min()), str(cut - pd.Timedelta(minutes=1))],
           "test_period": [str(cut), str(obs["ts"].max())],
           "n_train": int(len(train)), "n_test": int(len(test)),
           "model": _err(y, p),
           "baseline_persistence": _err(y, test["u0"].to_numpy()),
           "baseline_history": _err(y, test["hist_dow_hour"].to_numpy()),
           "by_horizon": {}}
    for lo, hi in ((0.5, 2), (2, 4), (4, 8.01)):
        m = (test["horizon"] >= lo) & (test["horizon"] < hi)
        if m.sum():
            out["by_horizon"][f"{lo:g}-{min(hi, 8):g}h"] = {
                "n": int(m.sum()), "model": _err(y[m], p[m]), "persistence": _err(y[m], test["u0"].to_numpy()[m]),
                "history": _err(y[m], test["hist_dow_hour"].to_numpy()[m])}
    # 상태(여유/보통/혼잡) 판정 정확도: 모델 vs 지금 상태 유지
    true_st = status_of(y, test["y_full"].to_numpy().astype(float))
    pers_st = status_of(test["u0"].to_numpy(), test["full0"].to_numpy().astype(float))
    pred_st = status_of(p, pf)
    out["status_accuracy"] = {"model": float(np.mean(pred_st == true_st)), "persistence": float(np.mean(pers_st == true_st))}
    from sklearn.metrics import roc_auc_score, precision_score, recall_score
    yf = test["y_full"].to_numpy()
    out["full_detection"] = {"auc": float(roc_auc_score(yf, pf)) if yf.min() != yf.max() else None,
                             "precision": float(precision_score(yf, pf >= 0.5, zero_division=0)),
                             "recall": float(recall_score(yf, pf >= 0.5, zero_division=0)),
                             "persistence_precision": float(precision_score(yf, test["full0"], zero_division=0)),
                             "persistence_recall": float(recall_score(yf, test["full0"], zero_division=0)),
                             "base_rate": float(yf.mean())}
    imp = pd.Series(reg.booster_.feature_importance("gain"), index=FEATURES)
    out["importance"] = (imp / imp.sum()).round(4).sort_values(ascending=False).to_dict()
    # 같은 조건으로 XGBoost와 비교 (설치돼 있을 때만)
    try:
        xr, xc = fit(train, "xgboost")
        out["xgboost"] = _score(test, xr, xc)
    except ImportError:
        out["xgboost"] = None
    out["one_hour"] = evaluate_one_hour(obs, price_map, asos=asos)
    # 날씨 효과: 날씨 열을 뺀 모델과 비교
    out["weather_coverage"] = float(train["temp"].notna().mean())
    if out["weather_coverage"] > 0.5:
        keep = [f for f in FEATURES if f not in weather.COLS]
        r0, _ = _models()
        tr = train.sample(min(len(train), MAX_TRAIN), random_state=0)
        r0.fit(tr[keep], tr["y"], categorical_feature=["region"])
        out["without_weather"] = _err(test["y"].to_numpy(), np.clip(r0.predict(test[keep]), 0, 1))
    return out


# ─────────────────────────────────────────────────────────────
# 5. 운영: 전체 기록으로 학습 → 마지막 수집 + 1시간 예측
# ─────────────────────────────────────────────────────────────
def forecast(history_dir: str, horizon_h: float = 1.0, metrics: dict | None = None) -> dict:
    obs = observations(history_dir)
    price_map = station_prices(history_dir)
    prof = build_profile(obs)
    asos = weather.load_asos(history_dir, obs["ts"].min().to_pydatetime(), obs["ts"].max().to_pydatetime())
    pairs = prepare(make_pairs(obs), prof, price_map, asos)
    reg, clf = fit(pairs)
    last = obs.loc[obs.groupby("region")["ts"].transform("max") == obs["ts"]].dropna(subset=["u"])
    now = last.rename(columns={"ts": "ts0", "u": "u0", "f": "fault0"}).assign(usable0=lambda d: d["a"] + d["c"])
    gen = obs["ts"].max()
    now["ts_t"] = now["ts0"] + pd.Timedelta(hours=horizon_h)
    fcst = weather.forecast_now(gen.to_pydatetime())
    X = prepare(now, prof, price_map, asos, fcst)
    w = (metrics or {}).get("one_hour", {}).get("blend_weight", DEFAULT_BLEND) if horizon_h <= 1.5 else 0.0
    u = w * X["u0"].to_numpy() + (1 - w) * np.clip(reg.predict(X[FEATURES]), 0, 1)
    pf = w * X["full"].to_numpy() + (1 - w) * clf.predict_proba(X[FEATURES])[:, 1]
    st = status_of(u, pf)
    preds = [{"station_id": s, "speed": "fast" if f else "slow", "utilization": round(float(v), 4),
              "p_full": round(float(q), 4), "status": str(k)}
             for s, f, v, q, k in zip(X["statId"], X["fast"], u, pf, st)]
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:00") + KST_OFFSET
    out = {"schema_version": 1, "model_name": MODEL_NAME, "generated_at": iso(gen),
           "target_time": iso(gen + pd.Timedelta(hours=horizon_h)),
           "horizon_hours": horizon_h, "n_train_pairs": int(len(pairs)), "blend_weight": w,
           "data_period": [iso(obs["ts"].min()), iso(gen)],
           "weather": {"train_coverage": round(float(pairs["temp"].notna().mean()), 3),
                       "forecast_available": bool(X["temp"].notna().any())},
           "predictions": preds}
    if metrics:
        out["metrics"] = metrics
    return out
