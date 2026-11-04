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


def dense_windows(history_dir: str | Path, max_gap_min: int = 35) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """세션 기록이 촘촘했던(수집 간격 ≤35분) 구간 목록 [(시작, 끝), ...]. 이 구간 안에 끝난 충전은 빠짐없이 기록됐다고 본다.
    (수집 간격이 길면 그사이 한 충전기에서 끝난 충전 여러 건 중 마지막 것만 남는다. 수집이 한 번 끊겨도 앞 구간은 그대로 쓴다)"""
    meta = Path(history_dir) / "sessions" / "meta.json"
    runs = json.loads(meta.read_text(encoding="utf-8")).get("runs", []) if meta.exists() else []
    t = pd.to_datetime(pd.Series(sorted(set(runs))), errors="coerce").dropna().reset_index(drop=True)
    out, start = [], None
    for i in range(1, len(t)):
        if t[i] - t[i - 1] <= pd.Timedelta(minutes=max_gap_min):
            start = t[i - 1] if start is None else start
            end = t[i]
        elif start is not None:
            out.append((start, end))
            start = None
    if start is not None:
        out.append((start, end))
    return out


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


LAG_H = {"fast": 3, "slow": 12}   # 시작한 충전이 끝나 기록될 때까지 기다리는 시간 (급속 p99, 완속 약 p95)
PRIOR_HOURS = 24.0                 # 충전소 실측 건수와 공식 추정을 섞는 강도: 공식 추정을 '하루치 관측'만큼 믿음


def complete_hours(windows: list | None, cutoff: pd.Timestamp, speed: str) -> list[pd.Timestamp]:
    """촘촘한 기록 구간 안에서, 그 시간에 시작한 충전이 모두 끝나 기록됐다고 볼 수 있는 1시간 칸들 (cutoff 이전)"""
    lag = pd.Timedelta(hours=1 + LAG_H[speed])
    return [h for a, b in (windows or []) for h in pd.date_range(a.ceil("h"), b, freq="h") if h + lag <= min(b, cutoff)]


def recordable_stations(sessions: pd.DataFrame, speed: str) -> set:
    """세션 시각(lastTsdt/lastTedt)을 한 번이라도 준 충전소. 운영사가 이 값을 안 주면 실측 건수를 셀 수 없다."""
    sp = sessions["speed"].map(SPEED_NAME).fillna(sessions["speed"])
    return set(sessions.loc[sp == speed, "statId"])


def occupancy_factor(obs: pd.DataFrame, sessions: pd.DataFrame, windows: list | None, cutoff: pd.Timestamp,
                     speed: str, min_session_hours: float = 50.0) -> float:
    """상태가 '충전 중'인 시간 ÷ 세션 기록(시작~종료)이 덮는 시간. 충전이 끝나도 차를 꽂아 둔 동안 '충전 중'으로 남아
    L̄ ÷ W가 시작 건수를 부풀리므로 이 비율로 W를 늘린다. 세션을 기록하는 충전소만, cutoff 이전 촘촘한 구간에서 잰다."""
    s = clean(sessions, cutoff)
    s = s[s["speed"] == speed]
    rec = set(s["statId"])
    status_h = session_h = 0.0
    for a, b in windows or []:
        b = min(b, cutoff)
        if b - a < pd.Timedelta(hours=2):
            continue
        o = obs[(obs["fast"] == (1 if speed == "fast" else 0)) & (obs["ts"] >= a) & (obs["ts"] < b) & obs["statId"].isin(rec)]
        n = o["ts"].nunique()
        if not n:
            continue
        status_h += o["c"].sum() * ((b - a).total_seconds() / 3600) / n
        over = (np.minimum(s["end"], b) - np.maximum(s["start"], a)).dt.total_seconds().clip(lower=0) / 3600
        session_h += over[s["statId"].isin(o["statId"].unique())].sum()
    return float(status_h / session_h) if session_h >= min_session_hours else 1.0


def daily_rates(obs: pd.DataFrame, sessions: pd.DataFrame, now: pd.Timestamp, windows: list | None = None,
                shape: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """충전소·종류별 하루 평균 시간당 충전 시작 건수 rate_h.
    1) 공식: L̄ ÷ (k × W)  (k: occupancy_factor, 촘촘한 기록이 없으면 1)
    2) 촘촘한 기록 구간에서 실제로 센 시작 건수 N이 있으면 섞음 (포아송-감마):
         rate = (N + PRIOR_HOURS × 공식) ÷ (E + PRIOR_HOURS),  E = 센 시간들의 시간대 모양 합
       세션 시각을 한 번도 안 준 충전소는 셀 수 없으므로 공식만 쓴다.
    now 이전 자료만 쓴다."""
    st, rg, sp, dist = durations(sessions, now)
    d = station_w(mean_charging(obs, now), st, rg, sp)
    d["rate_formula"] = np.where(d["W_min"] > 0, d["L_bar"] / (d["W_min"] / 60), np.nan)
    d["occupancy_k"], d["n_counted"], d["exposure_h"] = 1.0, 0.0, 0.0
    d["rate_h"] = d["rate_formula"]
    if windows:
        shape = shape if shape is not None else chungbuk_shape(obs, sessions, now)
        c = clean(sessions, now)
        for speed in ("fast", "slow"):
            m = d["speed"] == speed
            k = occupancy_factor(obs, sessions, windows, now, speed)
            d.loc[m, "occupancy_k"] = k
            d.loc[m, "rate_h"] = d.loc[m, "rate_formula"] / k
            hours = complete_hours(windows, now, speed)
            if not hours:
                continue
            E = float(sum(shape_at(shape, speed, h + pd.Timedelta(minutes=30)) for h in hours))
            cs = c[(c["speed"] == speed) & c["start"].dt.floor("h").isin(hours)]
            N = d.loc[m, "statId"].map(cs.groupby("statId").size()).fillna(0)
            rec = d.loc[m, "statId"].isin(recordable_stations(sessions, speed))
            blended = (N + PRIOR_HOURS * d.loc[m, "rate_h"]) / (E + PRIOR_HOURS)
            d.loc[m, "rate_h"] = np.where(rec, blended, d.loc[m, "rate_h"])
            d.loc[m, "n_counted"] = np.where(rec, N, 0.0)
            d.loc[m, "exposure_h"] = np.where(rec, E, 0.0)
    return d, dist


# ── 충전 1건당 충전량 (환경부 공공급속 실측, scripts/energy_summary.py) ────────
ENERGY_PATH = Path(__file__).resolve().parent.parent / "data" / "energy_fast_43.json"


def load_energy() -> dict | None:
    return json.loads(ENERGY_PATH.read_text(encoding="utf-8")) if ENERGY_PATH.exists() else None


def station_energy(energy: dict | None, station_id: str, speed: str) -> tuple[float | None, str]:
    """(1건당 kWh, 근거). 급속: 그 충전소 실측 → 없으면 충북 공공급속 평균. 완속: 실측 없음."""
    if not energy or speed != "fast":
        return None, "실측 없음"
    st = energy["stations"].get(station_id)
    if st:
        return st["kwh_per_session"], "충전소 실측"
    return energy["fast"]["kwh_per_session"], "충북 공공급속 평균"


def energy_block(energy: dict | None) -> dict | None:
    if not energy:
        return None
    return {"source": energy["source"], "period": energy["period"], "method": energy["method"],
            "fast": energy["fast"], "by_capacity_kw": energy["by_capacity_kw"],
            "slow": {"kwh_per_session": None, "note": "완속은 실측 자료 없음. 합성데이터 표본 참고값: "
                     + (f"평균 {energy['synthetic_check']['slow']['kwh_mean']}kWh (n={energy['synthetic_check']['slow']['n']}, 가상 자료)"
                        if energy.get("synthetic_check") else "없음")},
            "synthetic_check": energy.get("synthetic_check")}


# ── 출력: schema_version 2 ───────────────────────────────────
def build_v2(out1: dict, out2: dict, obs: pd.DataFrame, sessions: pd.DataFrame, interval_min: int = 15,
             span_h: float = 2.0, validation: dict | None = None, shape: dict | None = None,
             windows: list | None = None) -> dict:
    """1·2시간 예측(schema 1) + 수집 관측 + 세션 기록 → 15분 구간별 이용률·기대 신규 충전 건수"""
    gen = pd.Timestamp(out1["generated_at"]).tz_localize(None)
    shape = shape if shape is not None else chungbuk_shape(obs, sessions, gen)
    energy = load_energy()
    rates, dist = daily_rates(obs, sessions, gen, windows=windows, shape=shape)
    p1 = pd.DataFrame(out1["predictions"])
    p2 = pd.DataFrame(out2["predictions"])[["station_id", "speed", "utilization"]].rename(columns={"utilization": "u2"})
    d = p1.rename(columns={"utilization": "u1", "station_id": "statId"}).merge(
        p2.rename(columns={"station_id": "statId"}), on=["statId", "speed"], how="left")
    d["u2"] = d["u2"].fillna(d["u1"])
    d = d.merge(rates[["statId", "speed", "rate_h", "W_min", "level", "L_bar", "n", "rate_formula", "occupancy_k",
                       "n_counted", "exposure_h"]], on=["statId", "speed"], how="left")
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
        "model_name": out1["model_name"] + " + 신규 충전 건수 v2",
        "generated_at": out1["generated_at"],
        "simulation_start_at": out1["generated_at"],
        "interval_minutes": interval_min,
        "baseline": "existing_prices_without_new_policy",
        "demand_measure": "charging_starts",
        "method": {
            "utilization_mean": "구간 중앙 시각의 이용률. 지금 값(0시간)·1시간·2시간 예측을 직선으로 이음",
            "expected_new_sessions": (f"하루 평균 시작률 × 시간대 모양(충북 수집 기록을 역합성곱한 충전 시작 분포) × 구간 길이. 기대값이며 정수 실적이 아님. "
                                      f"하루 평균 시작률 = 최근 {L_DAYS}일 평균 충전 중 대수 ÷ (점유 보정 × 평균 충전 시간), "
                                      f"세션이 빠짐없이 기록된 시간대의 실제 시작 건수가 있으면 그것과 섞음 (공식 추정을 {PRIOR_HOURS:.0f}시간치 관측만큼 믿음)"),
            "occupancy_correction": "충전이 끝나도 차를 꽂아 둔 동안 상태가 '충전 중'으로 남아, 상태 기준 충전 시간이 세션 기록보다 김. 그 비율(occupancy_k)만큼 충전 시간을 늘림",
            "new_sessions_p10_p90": "기대값을 평균으로 한 포아송 분포의 10%·90% 분위 (낮음·높음 수요 시나리오)",
            "duration_source": f"충북 충전 세션 기록(lastTsdt/lastTedt) 최근 {WINDOW_DAYS}일. 충전소 기록 {MIN_SESSIONS}건 미만이면 시·군 평균, 그다음 충북 평균",
        },
        "duration_minutes": dist,
        "start_shape_hourly": {sp: hourly(shape, sp) for sp in ("fast", "slow")},
        "stations": [{"station_id": r.statId, "speed": r.speed, "mean_duration_minutes": round(float(r.W_min), 1),
                      "duration_level": r.level, "mean_charging": round(float(r.L_bar), 3),
                      "daily_starts": round(float(r.rate_h) * 24, 2),
                      "daily_starts_formula": round(float(r.rate_formula) * 24, 2) if pd.notna(r.rate_formula) else None,
                      "occupancy_k": round(float(r.occupancy_k), 3), "counted_starts": int(r.n_counted),
                      "counted_hours_shape_weighted": round(float(r.exposure_h), 1),
                      "mean_energy_kwh": station_energy(energy, r.statId, r.speed)[0],
                      "energy_source": station_energy(energy, r.statId, r.speed)[1]}
                     for r in d.drop_duplicates(["statId", "speed"]).itertuples() if pd.notna(r.rate_h)],
        "energy_kwh": energy_block(energy),
        "not_available": {"slow_energy_kwh": "완속 충전량 실측 자료 없음 (energy_kwh.slow 참고)",
                          "arrivals": "대기·포기 차량은 API에 없음 (충전을 시작한 건만 셈)"},
        "validation": validation or {},
        "predictions": rows,
    }


def _interp(d: pd.DataFrame, mid: float) -> np.ndarray:
    u0, u1, u2 = d["u_now"].to_numpy(float), d["u1"].to_numpy(float), d["u2"].to_numpy(float)
    return np.where(mid <= 1, u0 + (u1 - u0) * mid, u1 + (u2 - u1) * (mid - 1))


# ── 검증 ─────────────────────────────────────────────────────
def evaluate_starts(obs: pd.DataFrame, sessions: pd.DataFrame, windows: list | None, now: pd.Timestamp) -> dict:
    """실제 충북 충전 시작 건수로 검증 (충전소·종류·1시간 단위, 시간 순서대로 굴려 가며).
    촘촘한 기록 구간마다 그 구간 시작 시각 이전 자료로만 추정을 만들고, 그 구간의 '빠짐없이 기록된 시간'(complete_hours)에 맞혀 본다.
    비교: method(공식 보정 + 실측 섞기, 실제 운영 방식) / formula_only(L̄÷W만) / flat(시간대 모양 없이) / zero(모두 0건)"""
    if not windows:
        return {"note": "15분 간격 세션 기록이 아직 없음"}
    c = clean(sessions, now)
    out = {"windows": [[str(a), str(b)] for a, b in windows]}
    for speed in ("fast", "slow"):
        parts = []
        for a, b in windows:
            hours = complete_hours([(a, b)], now, speed)
            if not hours:
                continue
            past = sessions[sessions["end"] < a]
            shape = chungbuk_shape(obs, past, a)
            full, _ = daily_rates(obs, past, a, windows=windows, shape=shape)
            base, _ = daily_rates(obs, past, a)
            r = full[full["speed"] == speed][["statId", "rate_h"]].merge(
                base[base["speed"] == speed][["statId", "rate_h"]].rename(columns={"rate_h": "rate_formula"}), on="statId")
            r = r.dropna()
            g = r.merge(pd.DataFrame({"hour": hours}), how="cross")
            f = np.array([shape_at(shape, speed, h + pd.Timedelta(minutes=30)) for h in g["hour"]])
            g["method"], g["formula_only"], g["flat"] = g["rate_h"] * f, g["rate_formula"] * f, g["rate_h"]
            g["window"] = str(a)
            parts.append(g)
        if not parts:
            out[speed] = {"n_station_hours": 0, "note": f"아직 기록 부족 (끊김 없이 {LAG_H[speed] + 1}시간 이상 기록돼야 함)"}
            continue
        g = pd.concat(parts, ignore_index=True)
        cs = c[c["speed"] == speed]
        cnt = cs.groupby([cs["statId"], cs["start"].dt.floor("h").rename("hour")]).size().rename("obs").reset_index()
        m = g.merge(cnt, on=["statId", "hour"], how="left").fillna({"obs": 0})
        y = m["obs"].to_numpy(float)
        res = {"n_station_hours": int(len(m)), "n_hours": int(m["hour"].nunique()),
               "hours": [str(m["hour"].min()), str(m["hour"].max())], "observed_total": int(y.sum())}
        for k in ("method", "formula_only", "flat"):
            p = np.clip(m[k].to_numpy(float), 1e-9, None)
            with np.errstate(divide="ignore", invalid="ignore"):
                dev = 2 * np.mean(np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / p), 0) - (y - p))
            res[k] = {"estimated_total": round(float(p.sum()), 1), "poisson_deviance": round(float(dev), 4),
                      "RMSE": round(float(np.sqrt(np.mean((p - y) ** 2))), 4), "MAE": round(float(np.mean(np.abs(p - y))), 4)}
        res["zero"] = {"RMSE": round(float(np.sqrt(np.mean(y ** 2))), 4), "MAE": round(float(y.mean()), 4)}
        st = m.groupby("statId")[["obs", "method"]].sum()
        res["station_corr"] = round(float(st.corr().iloc[0, 1]), 4) if len(st) > 2 else None
        res["by_window"] = [{"window_start": wnd, "hours": int(x["hour"].nunique()), "observed": int(x["obs"].sum()),
                             "method": round(float(x["method"].sum()), 1), "formula_only": round(float(x["formula_only"].sum()), 1),
                             "method_RMSE": round(float(np.sqrt(np.mean((x["method"] - x["obs"]) ** 2))), 4),
                             "formula_only_RMSE": round(float(np.sqrt(np.mean((x["formula_only"] - x["obs"]) ** 2))), 4)}
                            for wnd, x in m.groupby("window")]
        hh = m.groupby(m["hour"].dt.hour)[["obs", "method"]].sum()
        res["hour_of_day_total_error"] = round(float((hh["method"] - hh["obs"]).abs().sum() / max(hh["obs"].sum(), 1)), 4)
        out[speed] = res
    return out
