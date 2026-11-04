import numpy as np
import pandas as pd

from utils import demand


def _obs(now):
    ts = pd.date_range(now - pd.Timedelta(days=3), now, freq="1h")
    rows = []
    for t in ts:   # A: 급속 4대 중 평균 2대 충전 중, B: 완속 2대 중 1대
        rows.append({"ts": t, "region": "43110", "statId": "A", "fast": 1, "a": 2, "c": 2, "f": 0})
        rows.append({"ts": t, "region": "43110", "statId": "B", "fast": 0, "a": 1, "c": 1, "f": 0})
    o = pd.DataFrame(rows)
    o["u"] = o["c"] / (o["a"] + o["c"])
    return o


def _sessions(now, n=10):
    rows = []
    for i in range(n):
        end = now - pd.Timedelta(hours=i + 1)
        rows.append({"region": "43110", "statId": "A", "chgerId": f"{i:02d}", "speed": "급속", "kw": 50.0,
                     "start": end - pd.Timedelta(minutes=30), "end": end, "minutes": 30.0})
        rows.append({"region": "43110", "statId": "B", "chgerId": f"{i:02d}", "speed": "완속", "kw": 7.0,
                     "start": end - pd.Timedelta(minutes=240), "end": end, "minutes": 240.0})
    # 미래 종료(기기 시계 오류)는 무시돼야 함
    rows.append({"region": "43110", "statId": "A", "chgerId": "99", "speed": "급속", "kw": 50.0,
                 "start": now, "end": now + pd.Timedelta(hours=5), "minutes": 300.0})
    return pd.DataFrame(rows)


def _out(now, u, h):
    iso = (now + pd.Timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:00+09:00")
    return {"model_name": "m", "generated_at": now.strftime("%Y-%m-%dT%H:%M:00+09:00"), "target_time": iso,
            "predictions": [{"station_id": "A", "speed": "fast", "utilization": u, "u_now": 0.5, "usable": 4},
                            {"station_id": "B", "speed": "slow", "utilization": u, "u_now": 0.5, "usable": 2}]}


def test_daily_rate_is_littles_law():
    now = pd.Timestamp("2026-11-03 12:00")
    rates, dist = demand.daily_rates(_obs(now), _sessions(now), now)
    r = rates.set_index("statId")
    assert r.loc["A", "level"] == "station" and abs(r.loc["A", "W_min"] - 30) < 1e-9   # 미래 종료 제외
    assert abs(r.loc["A", "rate_h"] - 2 / 0.5) < 1e-9        # 2대 충전 중 ÷ 0.5시간 = 시간당 4건
    assert abs(r.loc["B", "rate_h"] - 1 / 4) < 1e-9
    assert dist["fast"]["n"] == 10


def test_build_v2_contract():
    now = pd.Timestamp("2026-11-03 12:00")
    shape = {"fast": [1.0] * 96, "slow": [2.0] * 96}
    v2 = demand.build_v2(_out(now, 0.7, 1), _out(now, 0.9, 2), _obs(now), _sessions(now), shape=shape)
    assert v2["schema_version"] == 2 and v2["interval_minutes"] == 15 and v2["demand_measure"] == "charging_starts"
    p = pd.DataFrame(v2["predictions"])
    assert len(p) == 2 * 8 and set(p["speed"]) == {"fast", "slow"}
    a = p[p["station_id"] == "A"].reset_index(drop=True)
    assert a["interval_start"].iat[0] == "2026-11-03T12:00:00+09:00" and a["interval_end"].iat[-1] == "2026-11-03T14:00:00+09:00"
    assert np.allclose(a["expected_new_sessions"], 4 * 0.25)                        # 시간당 4건 × 15분
    assert abs(a["utilization_mean"].iat[0] - (0.5 + 0.2 * 0.125)) < 1e-4          # 0→1시간 직선 보간
    assert abs(a["utilization_mean"].iat[-1] - (0.7 + 0.2 * 0.875)) < 1e-4
    b = p[p["station_id"] == "B"]
    assert np.allclose(b["expected_new_sessions"], 0.25 * 2 * 0.25)               # 모양 2배 반영
    assert (p["new_sessions_p10"] <= p["new_sessions_p90"]).all()


def test_evaluate_starts_waits_for_data():
    now = pd.Timestamp("2026-11-03 12:00")
    v = demand.evaluate_starts(_obs(now), _sessions(now), [(now - pd.Timedelta(hours=1), now)], now)
    assert v["fast"]["n_station_hours"] == 0 and v["slow"]["n_station_hours"] == 0


def test_evaluate_starts_scores():
    now = pd.Timestamp("2026-11-03 12:00")
    windows = [(now - pd.Timedelta(hours=8), now)]
    v = demand.evaluate_starts(_obs(now), _sessions(now), windows, now)
    f = v["fast"]
    assert f["n_station_hours"] == 5 and f["n_hours"] == 5            # [H, H+4시간]이 구간 안에 드는 5개 시간
    assert {"method", "formula_only", "flat", "zero"} <= set(f)
    assert f["observed_total"] == 5                                   # 04:30 05:30 06:30 07:30 08:30 시작


def test_dense_windows_keep_every_stretch(tmp_path):
    import json
    (tmp_path / "sessions").mkdir()
    runs = ["2026-11-04 02:04", "2026-11-04 02:46", "2026-11-04 04:30", "2026-11-04 04:45", "2026-11-04 05:00",
            "2026-11-04 09:00", "2026-11-04 09:15"]
    (tmp_path / "sessions" / "meta.json").write_text(json.dumps({"runs": runs}))
    w = demand.dense_windows(tmp_path)
    assert w == [(pd.Timestamp("2026-11-04 04:30"), pd.Timestamp("2026-11-04 05:00")),
                 (pd.Timestamp("2026-11-04 09:00"), pd.Timestamp("2026-11-04 09:15"))]
    (tmp_path / "sessions" / "meta.json").write_text(json.dumps({"runs": runs[:1]}))
    assert demand.dense_windows(tmp_path) == []
    assert "note" in demand.evaluate_starts(pd.DataFrame(), pd.DataFrame(), [], pd.Timestamp("2026-11-04 06:00"))


def test_deconvolve_recovers_start_profile():
    # 시작이 18시 칸에 몰리고 충전 시간이 정확히 60분 → 18:00~19:00 사이 스냅숏에서만 충전 중
    rng = np.random.default_rng(0)
    tau = rng.uniform(0, 1440, 400)
    lam_true = np.zeros(96); lam_true[72:76] = 1.0          # 18:00~19:00 시작
    starts = (np.arange(96) * 15 + 7.5)
    occ = np.array([sum(lam_true[j] for j in range(96) if 0 <= (t - starts[j]) % 1440 < 60) for t in tau])
    lam = demand.deconvolve(tau, occ, np.full(500, 60.0), smooth=1.0)
    h = lam.reshape(24, 4).sum(1)
    assert h.argmax() == 18 and h[18] > 3 * h[[3, 9, 12]].max()


def test_chungbuk_shape_from_obs():
    now = pd.Timestamp("2026-11-03 12:00")
    obs = _obs(now)
    s = pd.concat([_sessions(now, 60)], ignore_index=True)
    sh = demand.chungbuk_shape(obs, s, now)
    assert len(sh["fast"]) == 96 and abs(np.mean(sh["fast"]) - 1) < 1e-3   # 평균 1
    assert demand.shape_at(sh, "fast", now) == sh["fast"][48]
    assert demand.shape_at({}, "fast", now) == 1.0


def _window_data(now, k_status=2, sess_minutes=30, n_sess=40):
    """4시간 구간: 급속 A는 상태상 늘 2대 충전 중, 세션은 30분짜리를 n_sess건 기록"""
    a = now - pd.Timedelta(hours=4)
    ts = pd.date_range(a, now, freq="15min", inclusive="left")
    obs = pd.DataFrame({"ts": ts, "region": "43110", "statId": "A", "fast": 1, "a": 2, "c": k_status, "f": 0})
    obs["u"] = obs["c"] / (obs["a"] + obs["c"])
    rows = []
    for i in range(n_sess):
        st = a + pd.Timedelta(minutes=6 * i)
        rows.append({"region": "43110", "statId": "A", "chgerId": f"{i % 2:02d}", "speed": "급속", "kw": 50.0,
                     "start": st, "end": st + pd.Timedelta(minutes=sess_minutes), "minutes": float(sess_minutes)})
    return obs, pd.DataFrame(rows), [(a, now)]


def test_occupancy_factor_is_status_over_session_hours():
    now = pd.Timestamp("2026-11-03 12:00")
    obs, ses, win = _window_data(now)
    # 상태: 2대 × 4시간 = 8 대·시간, 세션: 40건 × 0.5시간 = 20 대·시간 (구간 밖으로 넘친 부분 제외)
    k = demand.occupancy_factor(obs, ses, win, now, "fast", min_session_hours=1)
    done = ses[ses["end"] <= now + pd.Timedelta(minutes=10)]                    # 아직 안 끝난(미래 종료) 세션 제외
    over = sum((min(r.end, now) - r.start for r in done.itertuples()), pd.Timedelta(0)).total_seconds() / 3600
    assert abs(k - 8 / over) < 1e-6
    assert demand.occupancy_factor(obs, ses, win, now, "fast") == 1.0            # 세션 시간이 적으면 보정 안 함
    assert demand.occupancy_factor(obs, ses, [], now, "fast") == 1.0


def test_blend_counts_with_formula():
    now = pd.Timestamp("2026-11-03 12:00")
    obs, ses, _ = _window_data(now)
    win = [(now - pd.Timedelta(hours=30), now)]                                  # 충분히 긴 촘촘한 구간
    flat = {"fast": [1.0] * 96, "slow": [1.0] * 96}
    d, _ = demand.daily_rates(obs, ses, now, windows=win, shape=flat)
    r = d.set_index("statId").loc["A"]
    hours = demand.complete_hours(win, now, "fast")
    N = sum(1 for x in ses.itertuples() if x.start.floor("h") in hours)
    assert r["n_counted"] == N and r["exposure_h"] == len(hours)
    expect = (N + demand.PRIOR_HOURS * r["rate_formula"] / r["occupancy_k"]) / (len(hours) + demand.PRIOR_HOURS)
    assert abs(r["rate_h"] - expect) < 1e-9
    # 세션을 한 번도 안 준 충전소는 공식만
    obs2 = pd.concat([obs, obs.assign(statId="B")])
    d2, _ = demand.daily_rates(obs2, ses, now, windows=win, shape=flat)
    b = d2.set_index("statId").loc["B"]
    assert b["n_counted"] == 0 and abs(b["rate_h"] - b["rate_formula"] / b["occupancy_k"]) < 1e-9


def test_station_energy_lookup():
    e = {"stations": {"ME1": {"kwh_per_session": 31.0}}, "fast": {"kwh_per_session": 23.4}}
    assert demand.station_energy(e, "ME1", "fast") == (31.0, "충전소 실측")
    assert demand.station_energy(e, "XX", "fast") == (23.4, "충북 공공급속 평균")
    assert demand.station_energy(e, "ME1", "slow")[0] is None
    assert demand.station_energy(None, "ME1", "fast")[0] is None


def test_energy_file_is_sane():
    e = demand.load_energy()
    if e is None:
        return
    assert 10 < e["fast"]["kwh_per_session"] < 40 and e["fast"]["sessions"] > 10000
    caps = {int(k): v["kwh_per_session"] for k, v in e["by_capacity_kw"].items()}
    assert caps[50] < caps[100] <= caps[200]                     # 큰 충전기일수록 1건당 더 많이 충전
    assert all(5 < v["kwh_per_session"] < 80 for v in e["stations"].values())
