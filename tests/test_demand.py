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
    shape = {"fast": {"weekday": [1.0] * 24, "weekend": [1.0] * 24}, "slow": {"weekday": [2.0] * 24, "weekend": [2.0] * 24}}
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
    v = demand.evaluate_starts(_obs(now), _sessions(now), now - pd.Timedelta(hours=1), now)
    assert v["fast"]["n_station_hours"] == 0 and v["slow"]["n_station_hours"] == 0


def test_evaluate_starts_scores():
    now = pd.Timestamp("2026-11-03 12:00")
    since = now - pd.Timedelta(hours=8)
    v = demand.evaluate_starts(_obs(now), _sessions(now), since, now,
                               shape={"fast": {"weekday": [1.0] * 24, "weekend": [1.0] * 24}, "slow": {"weekday": [1.0] * 24, "weekend": [1.0] * 24}})
    assert v["fast"]["n_station_hours"] == 5 and "MAE" in v["fast"]


def test_recording_since_uses_latest_dense_stretch(tmp_path):
    import json
    (tmp_path / "sessions").mkdir()
    runs = ["2026-11-04 02:04", "2026-11-04 02:46", "2026-11-04 04:30", "2026-11-04 04:45", "2026-11-04 05:00"]
    (tmp_path / "sessions" / "meta.json").write_text(json.dumps({"runs": runs}))
    assert demand.recording_since(tmp_path) == pd.Timestamp("2026-11-04 04:30")
    (tmp_path / "sessions" / "meta.json").write_text(json.dumps({"runs": runs[:1]}))
    assert demand.recording_since(tmp_path) is None
    assert "note" in demand.evaluate_starts(pd.DataFrame(), pd.DataFrame(), None, pd.Timestamp("2026-11-04 06:00"))
