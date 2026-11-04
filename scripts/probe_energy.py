"""충전량 관련 공공 API 3종 응답 확인 (인증키는 EV_API_KEY 환경변수, 출력에 넣지 않음)"""
from __future__ import annotations

import json
import os

import requests

KEY = requests.utils.unquote(os.environ.get("EV_API_KEY", ""))
CALLS = {
    "synthetic_sessions": ("https://apis.data.go.kr/B552584/elcarChrsChargRcngSyrdt/getList",
                           {"pageNo": 1, "numOfRows": 5, "rgnNm": "충청북도", "returnType": "JSON"}),
    "synthetic_sessions_all": ("https://apis.data.go.kr/B552584/elcarChrsChargRcngSyrdt/getList",
                               {"pageNo": 1, "numOfRows": 1, "returnType": "JSON"}),
    "public_fast_monthly": ("https://apis.data.go.kr/B552584/pbnstFstChrgrChgcpcyInfo/getYrMnChgcpcyInfo",
                            {"pageNo": 1, "numOfRows": 5, "rgnNm": "충청북도", "returnType": "JSON"}),
    "public_fast_monthly_all": ("https://apis.data.go.kr/B552584/pbnstFstChrgrChgcpcyInfo/getYrMnChgcpcyInfo",
                                {"pageNo": 1, "numOfRows": 3, "returnType": "JSON"}),
    "energy_agency_fast": ("https://apis.data.go.kr/B553530/TRANSPORTATION/ELECTRIC_CHARGING",
                           {"pageNo": 1, "numOfRows": 5, "apiType": "json", "q1": "충청북도"}),
    "energy_agency_fast_all": ("https://apis.data.go.kr/B553530/TRANSPORTATION/ELECTRIC_CHARGING",
                               {"pageNo": 1, "numOfRows": 3, "apiType": "json"}),
}
for name, (url, params) in CALLS.items():
    try:
        r = requests.get(url, params={"serviceKey": KEY, **params}, timeout=30)
        body = r.text.replace(KEY, "[KEY]") if KEY else r.text
        print(f"=== {name} HTTP {r.status_code}\n{body[:3000]}\n")
    except Exception as e:
        print(f"=== {name} ERROR {type(e).__name__}: {str(e).replace(KEY, '[KEY]')[:300]}\n")
