"""
충전 시작 시간대 모양 (utils/demand.py) 만들기. 한 번만 돌리면 되고 결과는 models/start_shape.json.

    python -m scripts.train_start_shape --excel "서울시 소유 전기차충전기 일별 시간별 충전현황(2025년).xlsx"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils import demand, seoul


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", type=Path, required=True)
    args = ap.parse_args(argv)
    shape = demand.build_shape(seoul.load_sessions(args.excel))
    demand.SHAPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    demand.SHAPE_PATH.write_text(json.dumps(shape, ensure_ascii=False), encoding="utf-8")
    print(f"✔ {demand.SHAPE_PATH} {shape['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
