"""
AI 충전 수요 예측 실행: 쌓인 기록으로 LightGBM을 학습해 마지막 수집 1시간 뒤 이용률을 예측하고
data/forecast.json 형식(schema_version 1)으로 저장한다.

성능 평가(마지막 5일을 떼어 둔 검증)는 시간이 걸리므로 하루 한 번만 다시 하고, 결과는 기록 폴더의
forecast_metrics.json에 보관해 매번 예측 파일에 함께 싣는다.

사용법 (프로젝트 루트에서):
    python -m scripts.forecast --history hist/history --out _site/data/forecast.json
    python -m scripts.forecast --history hist/history --out data/forecast.json --evaluate   # 평가 강제
    python -m scripts.forecast --history hist/history --metrics-only                         # 평가만 (하루 한 번)
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from utils import demand, forecast as F, sessions, weather

KST = timezone(timedelta(hours=9))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="충전소별 1시간 후 이용률 예측")
    ap.add_argument("--history", required=True, help="기록 폴더 (history/)")
    ap.add_argument("--out", help="예측 JSON 저장 경로 (--metrics-only면 생략)")
    ap.add_argument("--metrics-only", action="store_true", help="성능 평가만 (오래됐을 때) 하고 예측은 하지 않음")
    ap.add_argument("--evaluate", action="store_true", help="성능 평가를 지금 다시 함")
    ap.add_argument("--eval-every-hours", type=float, default=24)
    args = ap.parse_args(argv)

    hist = Path(args.history)
    mpath = hist / "forecast_metrics.json"
    metrics = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    stale = not metrics or (datetime.now(KST) - datetime.fromisoformat(metrics["evaluated_at"])) > timedelta(hours=args.eval_every_hours)
    t0 = time.time()
    if args.evaluate or stale:
        obs = F.observations(str(hist))
        asos = weather.load_asos(str(hist), obs["ts"].min().to_pydatetime(), obs["ts"].max().to_pydatetime())
        metrics = F.evaluate(obs, F.station_prices(str(hist)), asos=asos)
        metrics["evaluated_at"] = datetime.now(KST).isoformat(timespec="minutes")
        mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✔ 성능 평가 ({time.time() - t0:.0f}초): RMSE 모델 {metrics['model']['RMSE']:.3f} / "
              f"현재값 유지 {metrics['baseline_persistence']['RMSE']:.3f} / 과거 평균 {metrics['baseline_history']['RMSE']:.3f}")
    if args.metrics_only:
        return 0
    outs = F.forecast(str(hist), metrics=metrics, horizons=(1.0, 2.0))
    base = Path(args.out)
    base.parent.mkdir(parents=True, exist_ok=True)
    for h, out in outs.items():   # 1시간: forecast.json (시뮬레이션 탭 형식), 2시간: forecast_2h.json
        p = base if h == 1.0 else base.with_name(base.stem + "_2h" + base.suffix)
        p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"✔ {p} 예측 {len(out['predictions'])}건, 대상 {out['target_time']} ({time.time() - t0:.0f}초)")
    # 시뮬레이션 v2 계약: 15분 구간별 이용률 + 기대 신규 충전 건수 (세션 기록이 있을 때만)
    sess = sessions.load(hist)
    if len(sess):
        gen = pd.Timestamp(outs[1.0]["generated_at"]).tz_localize(None)
        obs = F.observations(str(hist))
        windows = demand.dense_windows(hist)
        try:
            val = demand.evaluate_starts(obs, sess, windows, gen)
        except Exception as e:   # 검증 실패가 예측 파일을 막지 않게
            val = {"error": f"{type(e).__name__}: {e}"}
        v2 = demand.build_v2(outs[1.0], outs[2.0], obs, sess, validation=val, windows=windows)
        p = base.with_name(base.stem + "_v2" + base.suffix)
        p.write_text(json.dumps(v2, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"✔ {p} 15분 구간 {len(v2['predictions'])}행, 충전 시간 {v2['duration_minutes']}, 검증 {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
