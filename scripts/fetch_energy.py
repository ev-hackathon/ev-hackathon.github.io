"""
충전량 자료 받기 (인증키는 EV_API_KEY 환경변수, 출력·파일에 넣지 않음)
  1) 환경부 공공급속 충전기 월별 충전실적 — 충북 전부 (충전기·연월별 누적 충전량 kWh, 충전 횟수, 충전 시간)
  2) 환경부 충전건별 충전량 합성데이터 — 충북 표본 (무작위 페이지)
결과: <out>/public_fast_monthly_43.json, <out>/synthetic_sessions_43_sample.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import requests

MONTHLY = "https://apis.data.go.kr/B552584/pbnstFstChrgrChgcpcyInfo/getYrMnChgcpcyInfo"
SYNTH = "https://apis.data.go.kr/B552584/elcarChrsChargRcngSyrdt/getList"


def get(url, key, **params):
    for attempt in range(4):
        try:
            r = requests.get(url, params={"serviceKey": key, "returnType": "JSON", **params}, timeout=60)
            body = r.json()["body"]
            return body.get("items") or [], int(body.get("totalCount") or 0)
        except Exception as e:
            print(f"  재시도 {attempt + 1}: {type(e).__name__}")
            time.sleep(3 * (attempt + 1))
    return [], 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--synth-pages", type=int, default=60)
    args = ap.parse_args(argv)
    key = requests.utils.unquote(os.environ["EV_API_KEY"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows, total = get(MONTHLY, key, pageNo=1, numOfRows=100, rgnNm="충청북도")
    pages = (total + 99) // 100
    for p in range(2, pages + 1):
        items, _ = get(MONTHLY, key, pageNo=p, numOfRows=100, rgnNm="충청북도")
        rows += items
    (out / "public_fast_monthly_43.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"✔ 공공급속 월별 실적 충북 {len(rows)}/{total}행 ({pages}쪽)")

    _, total = get(SYNTH, key, pageNo=1, numOfRows=1, rgnNm="충청북도")
    random.seed(42)
    sample = []
    for p in sorted(random.sample(range(1, max(2, total // 100)), min(args.synth_pages, max(1, total // 100 - 1)))):
        items, _ = get(SYNTH, key, pageNo=p, numOfRows=100, rgnNm="충청북도")
        sample += [dict(i, _page=p) for i in items]
    (out / "synthetic_sessions_43_sample.json").write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    print(f"✔ 합성 충전건 충북 표본 {len(sample)}행 (전체 {total}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
