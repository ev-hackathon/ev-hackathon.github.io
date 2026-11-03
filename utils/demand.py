"""
신규 충전 수요(충전 시작 건수) 추정 — 시뮬레이션 v2 계약용 (박민선 docs/simulation-report.md 2장). 모두 충북 데이터.

  기대 신규 건수(충전소, 15분 구간) = 하루 평균 시작률 λ̄ × 시간대 모양 shape(급속/완속, 시각) × 구간 길이

1) 하루 평균 시작률 λ̄ = L̄ ÷ W  (리틀의 법칙. 하루 평균으로 보면 정확히 성립)
   L̄ : 최근 14일 그 충전소에서 평균 몇 대가 충전 중이었나 (시간대별 평균을 24시간에 고르게 평균, 수집 간격 편향 제거)
   W  : 충전 1건 평균 시간. 충북 충전 세션 기록(lastTsdt/lastTedt) 최근 30일.
        충전소 기록 5건 이상이면 충전소 평균, 아니면 시·군 평균, 그다음 충북 평균.
2) 시간대 모양 (충북 전체, 15분 칸 96개, 하루 평균 1): 역합성곱으로 푼다.
   어느 시각의 충전 중 대수 = 그 전에 시작한 충전 중 아직 안 끝난 것의 합
     L(τ) = Σ_j λ_j × P(칸 j에서 시작한 충전이 τ에 아직 충전 중)    (24시간 순환, 충전 시간 분포는 충북 세션 기록)
   충북 전 충전소의 수집 시각별 평균 충전 중 대수(3주)로 λ_j ≥ 0 을 매끄럽게(2차 차분 벌점) 최소제곱으로 푼다.
   서울 2025 충전 이력(정답 시작 시각을 앎)에서 띄엄띄엄한 스냅숏 160개만으로 시작 분포를 복원해 확인했다
   (급속 상관 0.99, 완속 0.80~0.93 — scripts/eval_starts_seoul.py).
   충북 세션 기록은 지금까지 '충전기마다 마지막 1건'이라 시작 시각이 저녁으로 쏠려 직접 세지 않는다.

순간값으로 λ(t) = L(t) ÷ W 를 쓰지 않는 이유: 완속은 밤새 꽂아 둔 차 때문에 새벽 L이 높지만 새벽에 시작하는 충전은 적다.
평일/주말은 아직 주말 수집이 적어 나누지 않는다.
검증: 15분 간격 세션 기록이 쌓인 뒤 실제 시간당 시작 건수와 비교(evaluate_starts).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson


MIN_SESSIONS = 5
WINDOW_DAYS = 30
L_DAYS = 14
SPEED_NAME = {"급속": "fast", "완속": "slow"}


def recording_since(history_dir: str | Path, max_gap_min: int = 35) -> pd.Timestamp | None:
    """가장 최근의 '촘촘한 수집' 구간 시작 시각. 이 뒤로 끝난 충전은 빠짐없이 기록됐다고 본다.
    (수집 간격이 길면 그사이 한 충전기에서 끝난 충전 여러 건 중 마지막 것만 남는다)"""
    meta = Path(history_dir) / "sessions" / "meta.json"
    runs = json.loads(meta.read_text(encoding="utf-8")).get("runs", []) if meta.exists() else []
    if len(runs) < 2:
        return None
    t = pd.to_datetime(pd.Series(sorted(set(runs))))
    gaps = t.diff() > pd.Timedelta(minutes=max_gap_min)
    start = t[gaps].iloc[-1] if gaps.any() else t.iloc[0]
    return start if start < t.iloc[-1] else None


# ── 시간대 모양 (충북, 역합성곱) ─────────────────────────────
BINS = 96
SMOOTH = 100.0   # 서울 검증에서 가장 잘 복원된 값 (3·10·30·100 비교)


def survival(minutes: np.ndarray):
    m = np.sort(np.clip(np.asarray(minutes, float), 0, 24 * 60 - 1))
    return lambda x: 1 - np.searchsorted(m, x, side="right") / len(m)


def deconvolve(tau_min: np.ndarray, occupancy: np.ndarray, minutes: np.ndarray, smooth: float = SMOOTH) -> np.ndarray:
    """수집 시각(하루 중 분)별 충전 중 대수 + 충전 시간 표본 → 15분 칸별 시작 건수(같은 단위). 24시간 순환."""
    from scipy.optimize import nnls
    S = survival(minutes)
    tau = np.asarray(tau_min, float)
    u = np.arange(15) + 0.5
    A = np.zeros((len(tau), BINS))
    for j in range(BINS):
        lag = (tau[:, None] - (j * 15 + u)[None, :]) % 1440
        A[:, j] = S(lag).mean(axis=1)
    D = np.zeros((BINS, BINS))
    for i in range(BINS):
        D[i, i], D[i, (i - 1) % BINS], D[i, (i + 1) % BINS] = -2, 1, 1
    lam, _ = nnls(np.vstack([A, np.sqrt(len(tau)) * smooth * A.mean() * D]),
                  np.concatenate([np.asarray(occupancy, float), np.zeros(BINS)]))
    return lam


def chungbuk_shape(obs: pd.DataFrame, sessions: pd.DataFrame, now: pd.Timestamp) -> dict:
    """{fast|slow: 96개(평균 1)} + 근거. obs 전체 기간의 충북 평균 충전 중 대수와 충북 충전 시간 분포로 푼다."""
    s = clean(sessions, now)
    o = obs[obs["ts"] <= now]
    out = {"source": "충북 수집 기록 역합성곱", "bins_minutes": 15, "snapshots": {}}
    for sp, f in (("fast", 1), ("slow", 0)):
        t = o[o["fast"] == f].groupby("ts").agg(c=("c", "sum"), n=("statId", "size"))
        mins = s.loc[s["speed"] == sp, "minutes"].to_numpy()
        if len(t) < 24 or len(mins) < 50:
            continue
        lam = deconvolve((t.index.hour * 60 + t.index.minute).to_numpy(), (t["c"] / t["n"]).to_numpy(), mins)
        if lam.mean() <= 0:
            continue
        out[sp] = [round(float(v), 4) for v in lam / lam.mean()]
        out["snapshots"][sp] = int(len(t))
    return out


def shape_at(shape: dict | None, speed: str, ts: pd.Timestamp) -> float:
    if not shape or speed not in shape:
        return 1.0
    return shape[speed][(ts.hour * 60 + ts.minute) // 15]


def hourly(shape: dict, speed: str) -> list[float]:
    return [round(float(np.mean(shape[speed][h * 4:h * 4 + 4])), 2) for h in range(24)] if speed in shape else []


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
    gen = pd.Timestamp(out1["generated_at"]).tz_localize(None)
    shape = shape if shape is not None else chungbuk_shape(obs, sessions, gen)
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
            "expected_new_sessions": f"하루 평균 시작률(최근 {L_DAYS}일 평균 충전 중 대수 ÷ 평균 충전 시간) × 시간대 모양(충북 수집 기록을 역합성곱한 충전 시작 분포) × 구간 길이. 기대값이며 정수 실적이 아님",
            "new_sessions_p10_p90": "기대값을 평균으로 한 포아송 분포의 10%·90% 분위 (낮음·높음 수요 시나리오)",
            "duration_source": f"충북 충전 세션 기록(lastTsdt/lastTedt) 최근 {WINDOW_DAYS}일. 충전소 기록 {MIN_SESSIONS}건 미만이면 시·군 평균, 그다음 충북 평균",
        },
        "duration_minutes": dist,
        "start_shape_hourly": {sp: hourly(shape, sp) for sp in ("fast", "slow")},
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
def evaluate_starts(obs: pd.DataFrame, sessions: pd.DataFrame, since: pd.Timestamp | None, now: pd.Timestamp,
                    shape: dict | None = None) -> dict:
    """빠짐없이 기록된 기간의 실제 시간당 충전 시작 건수 vs 추정 (충전소·종류·1시간 단위).
    급속은 3시간, 완속은 24시간 지난 시간대만 본다 (아직 안 끝난 충전은 기록 전이라).
    추정의 하루 평균 시작률은 검증 시간대 이전 기록으로만 계산한다."""
    if since is None:
        return {"note": "15분 간격 세션 기록이 아직 시작되지 않음"}
    s = clean(sessions, now)
    out = {"recording_since": str(since)}
    past = sessions[sessions["end"] < since]
    rates, _ = daily_rates(obs, past, since)
    shape = shape if shape is not None else chungbuk_shape(obs, past, since)
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
