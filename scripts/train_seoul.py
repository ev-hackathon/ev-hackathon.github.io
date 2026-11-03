"""
서울 사전학습 모델 학습 (utils/seoul.py). 한 번만 돌리면 되고 결과는 models/에 저장된다.

    python -m scripts.train_seoul --excel "서울시 소유 전기차충전기 일별 시간별 충전현황(2025년).xlsx"

분할(시간 순서): 학습 1~10월 / 조기 종료 검증 11월 / 테스트 12월
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from utils import seoul as S


def _err(y, p):
    return {"MAE": round(float(np.mean(np.abs(y - p))), 4), "RMSE": round(float(np.sqrt(np.mean((y - p) ** 2))), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, required=True)
    args = ap.parse_args(argv)

    data = S.features(S.hourly(S.load_sessions(args.excel)))
    train = data[data["slot"] < "2025-11-01"]
    valid = data[(data["slot"] >= "2025-11-01") & (data["slot"] < "2025-12-01")]
    test = data[data["slot"] >= "2025-12-01"]
    tr, va = S.drop_some_lags(train), S.drop_some_lags(valid, seed=S.SEED + 1)
    model = lgb.LGBMRegressor(n_estimators=2000, learning_rate=0.05, num_leaves=63, min_child_samples=200,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=S.SEED, verbose=-1)
    model.fit(tr[S.FEATURES], tr[S.TARGET], eval_set=[(va[S.FEATURES], va[S.TARGET])], eval_metric="l1",
              callbacks=[lgb.early_stopping(100, verbose=False)])
    y = test[S.TARGET].to_numpy()
    full = np.clip(model.predict(test[S.FEATURES]), 0, 1)
    sparse = np.clip(model.predict(S.drop_some_lags(test, seed=S.SEED + 2)[S.FEATURES]), 0, 1)
    metrics = {"stations": int(data["station"].nunique()), "n_train": int(len(train)), "n_test": int(len(test)),
               "best_iteration": int(model.best_iteration_),
               "test_dec_2025": {"model": _err(y, full), "model_sparse_lags": _err(y, sparse),
                                 "baseline_persistence": _err(y, test["util_now"].to_numpy())}}
    S.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(S.MODEL_PATH), num_iteration=model.best_iteration_)
    S.MODEL_PATH.with_name("seoul_lgbm_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
