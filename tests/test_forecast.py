import json
from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("lightgbm")

from scripts import forecast as cli
from utils import demo_data, forecast as F, history, logic

T0 = datetime(2026, 10, 20, 0, 0, tzinfo=logic.KST)


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    """증평군 가상 기록 5일치, 2시간 간격 + 하루는 30분 간격"""
    root = tmp_path_factory.mktemp("history")
    times = [T0 + timedelta(hours=h) for h in range(0, 24 * 5, 2)] + [T0 + timedelta(days=2, hours=9, minutes=30 * k) for k in range(1, 8)]
    for t in sorted(times):
        history.append("증평군", demo_data.generate("증평군", now=t.replace(tzinfo=None)), t, root)
    return root


def test_observations_and_pairs(hist):
    obs = F.observations(str(hist))
    assert {"ts", "statId", "fast", "u", "full", "prev_u", "recent24"} <= set(obs.columns)
    assert obs["u"].dropna().between(0, 1).all()
    pairs = F.make_pairs(obs)
    h = (pairs["ts_t"] - pairs["ts0"]).dt.total_seconds() / 3600
    assert h.between(F.MIN_H, F.MAX_H).all() and (h < 1.01).any()     # 30분 간격 날의 1시간 이하 짝 포함


def test_evaluate_reports_model_and_baselines(hist):
    obs = F.observations(str(hist))
    m = F.evaluate(obs, F.station_prices(str(hist)), test_days=1)
    for k in ("model", "baseline_persistence", "baseline_history"):
        assert np.isfinite(m[k]["RMSE"]) and m[k]["MAE"] <= m[k]["RMSE"] + 1e-9
    assert 0 <= m["status_accuracy"]["model"] <= 1


def test_forecast_json_contract(hist, tmp_path):
    out = tmp_path / "forecast.json"
    assert cli.main(["--history", str(hist), "--out", str(out), "--evaluate"]) == 0
    f = json.loads(out.read_text(encoding="utf-8"))
    assert f["schema_version"] == 1 and f["model_name"]
    gen = datetime.fromisoformat(f["generated_at"]); tgt = datetime.fromisoformat(f["target_time"])
    assert tgt - gen == timedelta(hours=1) and gen.utcoffset() == timedelta(hours=9)
    keys = {(p["station_id"], p["speed"]) for p in f["predictions"]}
    assert len(keys) == len(f["predictions"])                              # 충전소·속도 중복 없음
    for p in f["predictions"]:
        assert p["speed"] in ("fast", "slow") and 0 <= p["utilization"] <= 1 and 0 <= p["p_full"] <= 1
        assert p["status"] in ("여유", "보통", "혼잡")
    assert (hist / "forecast_metrics.json").exists() and "RMSE" in f["metrics"]["model"]


def test_weather_attach_maps_region_to_station_and_hour():
    import pandas as pd
    from utils import weather
    asos = pd.DataFrame({"stn": [131, 127], "ts": pd.to_datetime(["2026-10-20 13:00", "2026-10-20 13:00"]),
                         "temp": [15.0, 12.0], "rain": [0.0, 2.5], "humid": [60.0, 80.0]})
    df = pd.DataFrame({"region": [43110, 43770, 43150], "ts_t": pd.to_datetime(["2026-10-20 13:40"] * 3)})
    out = weather.attach(df, asos)
    assert out["temp"].tolist()[:2] == [15.0, 12.0] and out["rain"].iat[1] == 2.5   # 청주→131, 음성→127
    assert np.isnan(out["temp"].iat[2])                                              # 제천(221) 자료 없음


def test_two_hour_file_written(hist, tmp_path):
    out = tmp_path / "forecast.json"
    assert cli.main(["--history", str(hist), "--out", str(out)]) == 0
    f1 = json.loads(out.read_text(encoding="utf-8"))
    f2 = json.loads((tmp_path / "forecast_2h.json").read_text(encoding="utf-8"))
    assert f1["horizon_hours"] == 1.0 and f2["horizon_hours"] == 2.0
    assert datetime.fromisoformat(f2["target_time"]) - datetime.fromisoformat(f1["generated_at"]) == timedelta(hours=2)
    assert len(f1["predictions"]) == len(f2["predictions"])
