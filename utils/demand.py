"""
신규 충전 수요(충전 시작 건수) 추정 — 시뮬레이션 v2 계약용 (박민선 docs/simulation-report.md 2장)

  기대 신규 건수(충전소, 구간) = 하루 평균 시작률 λ̄ × 시간대 모양 shape(급속/완속, 평일/주말, 시각) × 구간 길이

1) 하루 평균 시작률 λ̄ = L̄ ÷ W  (리틀의 법칙. 하루 평균으로 보면 정확히 성립)
   L̄ : 최근 14일 그 충전소에서 평균 몇 대가 충전 중이었나 (시간대별 평균을 24시간에 고르게 평균, 수집 간격 편향 제거)
   W  : 충전 1건 평균 시간. 충북 충전 세션 기록(lastTsdt/lastTedt) 최근 30일.
        충전소 기록 5건 이상이면 충전소 평균, 아니면 시·군 평균, 그다음 충북 평균.
2) 시간대 모양: 서울시 소유 충전기 2025년 1년치 충전 21만 건의 시작 시각 분포 (models/start_shape.json).
   충북 세션은 지금까지 '충전기마다 마지막 1건'이라 시작 시각이 저녁으로 쏠려 모양 추정에 쓰지 않는다.
   빠짐없이 기록한 충북 세션이 14일 이상 쌓이면 충북 모양으로 바꿀 수 있다.

순간값으로 λ(t) = L(t) ÷ W 를 쓰지 않는 이유: 완속은 밤새 꽂아 둔 차 때문에 새벽 L이 높지만 새벽에 시작하는 충전은 적다.
검증: 세션 기록이 빠짐없이 쌓이기 시작한 시각 이후의 실제 시간당 시작 건수와 비교(evaluate_starts).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

import config

MIN_SESSIONS = 5
WINDOW_DAYS = 30
L_DAYS = 14
SHAPE_PATH = config.ROOT / "models" / "start_shape.json"
RECORDING_SINCE_DEFAULT = "2026-11-04 02:04:00"   # 세션 기록 첫 수집 (이후 끝난 충전은 빠짐없이 기록)
SPEED_NAME = {"급속": "fast", "완속": "slow"}


def recording_since(history_dir: str | Path) -> pd.Timestamp:
    meta = Path(history_dir) / "sessions" / "meta.json"
    if meta.exists():
        return pd.Timestamp(json.loads(meta.read_text(encoding="utf-8"))["recording_since"])
    return pd.Timestamp(RECORDING_SINCE_DEFAULT)


# ── 시간대 모양 (서울 2025) ───────────────────────────────────
def build_shape(seoul_sessions: pd.DataFrame) -> dict:
    """서울 세션(start, end, kwh) → {fast|slow: {weekday|weekend: 24개, 평균 1}}. 급속 ≥20kW, 완속 <15kW이면서 1시간 이상"""
    s = seoul_sessions.copy()
    dur = (s["end"] - s["start"]).dt.total_seconds() / 60
    kw = s["kwh"] / (dur / 60)
    s["speed"] = np.where((kw < 15) & (dur >= 60), "slow", np.where(kw >= 20, "fast", ""))
    s = s[s["speed"] != ""]
    s["we"] = np.where(s["start"].dt.dayofweek >= 5, "weekend", "weekday")
    out = {"source": "서울시 소유 전기차충전기 일별 시간별 충전현황(2025년)", "n": {}}
    for sp, g in s.groupby("speed"):
        out["n"][sp] = int(len(g))
        out[sp] = {}
        for we, h in g.groupby("we"):
            c = h["start"].dt.hour.value_counts().reindex(range(24), fill_value=0).to_numpy(float)
            out[sp][we] = [round(float(v), 4) for v in c / c.mean()]
    return out


def load_shape() -> dict | None:
    return json.loads(SHAPE_PATH.read_text(encoding="utf-8")) if SHAPE_PATH.exists() else None


def shape_at(shape: dict | None, speed: str, ts: pd.Timestamp) -> float:
    if not shape:
        return 1.0
    return shape[speed]["weekend" if ts.dayofweek >= 5 else "weekday"][ts.hour]


# ── 충전 시간 W ──────────────────────────────────────────────
def clean(sessions: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    s = sessions.copy()
    s["speed"] = s["speed"].map(SPEED_NAME).fillna(s["speed"])
    s = s[(s["end"] <= now + pd.Timedelta(minutes=10)) & (s["minutes"] >= 1)]
    return s[s["end"] >= now - pd.Timedelta(days=WINDOW_DAYS)]


def durations(sessions: pd.DataFrame, now: pd.Timestamp):
    """평균 충전 시간(분): 충전소별, 시·군별, 충북 전체, 그리고 종류별 분포"""
    s = clean(sessions, now)
    s["region"] = s["region"].astype(str)
    st = s.groupby(["statId", "speed", "region"])["minutes"].agg(W="mean", n="size").reset_index()
    rg = s.groupby(["region", "speed"])["minutes"].agg(W_region="mean", n_region="size").reset_index()
    sp = s.groupby("speed")["minutes"].mean().rename("W_all").reset_index()
    dist = {k: {"n": int(len(g)), "mean": round(float(g.mean()), 1),
                **{f"p{q}": round(float(g.quantile(q / 100)), 1) for q in (10, 25, 50, 75, 90)}}
            for k, g in s.groupby("speed")["minutes"]}
    return st, rg, sp, dist


def station_w(keys: pd.DataFrame, st, rg, sp) -> pd.DataFrame:
    """keys: statId, speed, region → W_min(분), level(station/region/all)"""
    d = keys.assign(region=keys["region"].astype(str)).merge(st, on=["statId", "speed", "region"], how="left") \
            .merge(rg, on=["region", "speed"], how="left").merge(sp, on="speed", how="left")
    ok_st = d["n"].fillna(0) >= MIN_SESSIONS
    ok_rg = d["n_region"].fillna(0) >= MIN_SESSIONS
    d["level"] = np.where(ok_st, "station", np.where(ok_rg, "region", "all"))
    d["W_min"] = np.where(ok_st, d["W"], np.where(ok_rg, d["W_region"], d["W_all"]))
    return d


# ── 하루 평균 충전 중 대수 L̄ ──────────────────────────────────
def mean_charging(obs: pd.DataFrame, now: pd.Timestamp, days: int = L_DAYS) -> pd.DataFrame:
    """statId, speed, region, L_bar(평균 충전 중 대수), usable(최근 정상 충전기 수)"""
    o = obs[(obs["ts"] > now - pd.Timedelta(days=days)) & (obs["ts"] <= now)].copy()
    o["speed"] = np.where(o["fast"] == 1, "fast", "slow")
    o["hour"] = o["ts"].dt.hour
    by_hour = o.groupby(["statId", "speed", "region", "hour"])["c"].mean()
    L = by_hour.groupby(["statId", "speed", "region"]).mean().rename("L_bar").reset_index()
    last = o.sort_values("ts").groupby(["statId", "speed"]).tail(1)
    L = L.merge(last.assign(usable=last["a"] + last["c"])[["statId", "speed", "usable"]], on=["statId", "speed"], how="left")
    L["region"] = L["region"].astype(str)
    return L


def daily_rates(obs: pd.DataFrame, sessions: pd.DataFrame, now: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    st, rg, sp, dist = durations(sessions, now)
    d = station_w(mean_charging(obs, now), st, rg, sp)
    d["rate_h"] = np.where(d["W_min"] > 0, d["L_bar"] / (d["W_min"] / 60), np.nan)   # 하루 평균 시간당 시작 건수
    return d, dist


# ── 출력: schema_version 2 ───────────────────────────────────
def build_v2(out1: dict, out2: dict, obs: pd.DataFrame, sessions: pd.DataFrame, interval_min: int = 15,
             span_h: float = 2.0, validation: dict | None = None, shape: dict | None = None) -> dict:
    """1·2시간 예측(schema 1) + 수집 관측 + 세션 기록 → 15분 구간별 이용률·기대 신규 충전 건수"""
    shape = shape if shape is not None else load_shape()
    gen = pd.Timestamp(out1["generated_at"]).tz_localize(None)
    rates, dist = daily_rates(obs, sessions, gen)
    p1 = pd.DataFrame(out1["predictions"])
    p2 = pd.DataFrame(out2["predictions"])[["station_id", "speed", "utilization"]].rename(columns={"utilization": "u2"})
    d = p1.rename(columns={"utilization": "u1", "station_id": "statId"}).merge(
        p2.rename(columns={"station_id": "statId"}), on=["statId", "speed"], how="left")
    d["u2"] = d["u2"].fillna(d["u1"])
    d = d.merge(rates[["statId", "speed", "rate_h", "W_min", "level", "L_bar", "n"]], on=["statId", "speed"], how="left")
    tz = out1["generated_at"][-6:]
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:00") + tz
    rows = []
    for k in range(int(round(span_h * 60 / interval_min))):
        a = gen + pd.Timedelta(minutes=interval_min * k)
        b = a + pd.Timedelta(minutes=interval_min)
        mid_h = (k + 0.5) * interval_min / 60
        mid_t = a + pd.Timedelta(minutes=interval_min / 2)
        u = np.clip(_interp(d, mid_h), 0, 1)
        f = np.array([shape_at(shape, s, mid_t) for s in d["speed"]])
        lam = d["rate_h"].to_numpy() * f * interval_min / 60
        lo, hi = poisson.ppf(0.1, np.nan_to_num(lam)), poisson.ppf(0.9, np.nan_to_num(lam))
        for i in range(len(d)):
            if not np.isfinite(lam[i]):
                continue
            rows.append({"station_id": d["statId"].iat[i], "speed": d["speed"].iat[i],
                         "interval_start": iso(a), "interval_end": iso(b),
                         "utilization_mean": round(float(u[i]), 4),
                         "expected_new_sessions": round(float(lam[i]), 3),
                         "new_sessions_p10": int(lo[i]), "new_sessions_p90": int(hi[i])})
    return {
        "schema_version": 2,
        "model_name": out1["model_name"] + " + 신규 충전 건수 v1",
        "generated_at": out1["generated_at"],
        "simulation_start_at": out1["generated_at"],
        "interval_minutes": interval_min,
        "baseline": "existing_prices_without_new_policy",
        "demand_measure": "charging_starts",
        "method": {
            "utilization_mean": "구간 중앙 시각의 이용률. 지금 값(0시간)·1시간·2시간 예측을 직선으로 이음",
            "expected_new_sessions": f"하루 평균 시작률(최근 {L_DAYS}일 평균 충전 중 대수 ÷ 평균 충전 시간) × 시간대 모양(서울 2025 충전 시작 분포) × 구간 길이. 기대값이며 정수 실적이 아님",
            "new_sessions_p10_p90": "기대값을 평균으로 한 포아송 분포의 10%·90% 분위 (낮음·높음 수요 시나리오)",
            "duration_source": f"충북 충전 세션 기록(lastTsdt/lastTedt) 최근 {WINDOW_DAYS}일. 충전소 기록 {MIN_SESSIONS}건 미만이면 시·군 평균, 그다음 충북 평균",
        },
        "duration_minutes": dist,
        "stations": [{"station_id": r.statId, "speed": r.speed, "mean_duration_minutes": round(float(r.W_min), 1),
                      "duration_level": r.level, "mean_charging": round(float(r.L_bar), 3),
                      "daily_starts": round(float(r.rate_h) * 24, 2)}
                     for r in d.drop_duplicates(["statId", "speed"]).itertuples() if pd.notna(r.rate_h)],
        "not_available": {"mean_energy_kwh": "공공 API에 충전량이 없음",
                          "arrivals": "대기·포기 차량은 API에 없음 (충전을 시작한 건만 셈)"},
        "validation": validation or {},
        "predictions": rows,
    }


def _interp(d: pd.DataFrame, mid: float) -> np.ndarray:
    u0, u1, u2 = d["u_now"].to_numpy(float), d["u1"].to_numpy(float), d["u2"].to_numpy(float)
    return np.where(mid <= 1, u0 + (u1 - u0) * mid, u1 + (u2 - u1) * (mid - 1))


# ── 검증 ─────────────────────────────────────────────────────
def evaluate_starts(obs: pd.DataFrame, sessions: pd.DataFrame, since: pd.Timestamp, now: pd.Timestamp,
                    shape: dict | None = None) -> dict:
    """빠짐없이 기록된 기간의 실제 시간당 충전 시작 건수 vs 추정 (충전소·종류·1시간 단위).
    급속은 3시간, 완속은 24시간 지난 시간대만 본다 (아직 안 끝난 충전은 기록 전이라).
    추정의 하루 평균 시작률은 검증 시간대 이전 기록으로만 계산한다."""
    shape = shape if shape is not None else load_shape()
    s = clean(sessions, now)
    out = {"recording_since": str(since)}
    rates, _ = daily_rates(obs, sessions[sessions["end"] < since], since)
    for speed, lag_h in (("fast", 3), ("slow", 24)):
        lo, hi = since.ceil("h"), (now - pd.Timedelta(hours=lag_h)).floor("h")
        if hi <= lo:
            out[speed] = {"n_station_hours": 0, "note": f"아직 기록 부족 (기록 시작 후 {lag_h}시간 이상 지나야 함)"}
            continue
        r = rates[rates["speed"] == speed].dropna(subset=["rate_h"])
        hours = pd.date_range(lo, hi, freq="h", inclusive="left")
        grid = r[["statId", "rate_h"]].merge(pd.DataFrame({"hour": hours}), how="cross")
        grid["est"] = grid["rate_h"] * [shape_at(shape, speed, h + pd.Timedelta(minutes=30)) for h in grid["hour"]]
        ss = s[(s["speed"] == speed) & (s["start"] >= lo) & (s["start"] < hi)]
        cnt = ss.groupby([ss["statId"], ss["start"].dt.floor("h").rename("hour")]).size().rename("obs").reset_index()
        m = grid.merge(cnt, on=["statId", "hour"], how="left").fillna({"obs": 0})
        out[speed] = {"n_station_hours": int(len(m)), "hours": [str(lo), str(hi)],
                      "observed_total": int(m["obs"].sum()), "estimated_total": round(float(m["est"].sum()), 1),
                      "MAE": round(float((m["est"] - m["obs"]).abs().mean()), 4),
                      "MAE_flat_rate": round(float((m["rate_h"] - m["obs"]).abs().mean()), 4),
                      "MAE_zero": round(float(m["obs"].mean()), 4)}
    return out
