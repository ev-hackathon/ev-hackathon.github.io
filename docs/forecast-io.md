# AI 수요 예측 모델 입출력 명세

시뮬레이션(B조)이 그대로 읽을 수 있도록 만든 예측 파일 3종입니다. GitHub Actions가 30분마다 새로 만듭니다.

| 파일 | 주소 | 내용 |
|---|---|---|
| v1 (1시간 후) | https://ev-hackathon.github.io/data/forecast.json | 충전소별 1시간 후 이용률 (시뮬레이션 v1 계약) |
| v1 (2시간 후) | https://ev-hackathon.github.io/data/forecast_2h.json | 같은 형식, 2시간 후 |
| v2 (15분 구간) | https://ev-hackathon.github.io/data/forecast_v2.json | 2시간 동안 15분 구간별 이용률 + 기대 신규 충전 건수 (시뮬레이션 v2 계약안) |

세 파일 모두 CORS가 열려 있어 다른 사이트에서 바로 `fetch`할 수 있습니다. 시각은 모두 한국 시간(+09:00)입니다.

## 1. 입력 (모델이 쓰는 데이터)

| 입력 | 출처 | 갱신 |
|---|---|---|
| 충전소별 빈·충전 중·고장 충전기 수 | 한국환경공단 충전기 상태 API, 충북 11개 시·군 | 15분 |
| 충전 세션 (충전기별 마지막 충전 시작·종료 시각) | 같은 API의 lastTsdt·lastTedt | 15분, 2026-11-04 02:04부터 |
| 운영사 회원가 | 무공해차 통합누리집 운영사별 충전요금 | 사이트 생성 시 |
| 기온·강수량·습도 | 기상청 ASOS(학습), 초단기예보(예측) | 1시간 |
| 서울시 충전 이력 2025년 1년치 | 서울 열린데이터광장 (사전학습·시간대 모양) | 고정 |

## 2. v1 출력 — `forecast.json` (schema_version 1, 아래 숫자는 형식 예시)

```json
{
  "schema_version": 1,
  "model_name": "통합 예측 v3 (충북 LightGBM + 서울 사전학습 + 현재값)",
  "generated_at": "2026-11-04T02:03:00+09:00",
  "target_time": "2026-11-04T03:03:00+09:00",
  "horizon_hours": 1.0,
  "predictions": [
    {"station_id": "CRCR0028", "speed": "fast", "utilization": 0.8123,
     "p_full": 0.61, "status": "혼잡", "u_now": 1.0, "usable": 1, "region": "43110"}
  ]
}
```

| 필드 | 뜻 |
|---|---|
| station_id | 공공데이터 충전소 ID (statId) |
| speed | fast 급속 / slow 완속. 한 충전소에 둘 다 있으면 두 줄 |
| utilization | target_time의 예상 이용률 = 충전 중 ÷ (빈 + 충전 중), 0~1 |
| p_full | 빈 충전기가 0대일 확률 |
| status | 여유 / 보통 / 혼잡 (관제 화면 기준과 같음) |
| u_now, usable | 지금(generated_at) 이용률과 정상 충전기 수 |
| region | 시·군 코드 |

시뮬레이션 v1은 station_id·speed·utilization만 읽고 나머지는 무시합니다. 박민선 님 사이트에서 이 주소로 불러와 실행되는 것을 확인했습니다(2026-11-04).

## 3. v2 출력 — `forecast_v2.json` (schema_version 2, 아래 숫자는 형식 예시)

박민선 님 `docs/simulation-report.md` 2장의 계약안 필드를 그대로 따릅니다.

```json
{
  "schema_version": 2,
  "generated_at": "2026-11-04T02:03:00+09:00",
  "simulation_start_at": "2026-11-04T02:03:00+09:00",
  "interval_minutes": 15,
  "baseline": "existing_prices_without_new_policy",
  "demand_measure": "charging_starts",
  "duration_minutes": {"fast": {"n": 2248, "mean": 44.0, "p10": 9.7, "p25": 19.3, "p50": 35.3, "p75": 51.7, "p90": 72.0},
                       "slow": {"n": 8648, "mean": 342.2, "p50": 305.1, "...": "..."}},
  "stations": [{"station_id": "EC430010", "speed": "fast", "mean_duration_minutes": 23.1,
                "duration_level": "station", "mean_charging": 1.623, "daily_starts": 101.23}],
  "predictions": [
    {"station_id": "EC430010", "speed": "fast",
     "interval_start": "2026-11-04T02:03:00+09:00", "interval_end": "2026-11-04T02:18:00+09:00",
     "utilization_mean": 0.21, "expected_new_sessions": 0.32,
     "new_sessions_p10": 0, "new_sessions_p90": 1}
  ]
}
```

| 필드 | 뜻 | 계산 |
|---|---|---|
| interval_start / end | 15분 구간, generated_at부터 2시간(8구간) | |
| utilization_mean | 구간 중앙 시각의 예상 이용률 | 지금 값·1시간 예측·2시간 예측을 직선으로 이음 |
| expected_new_sessions | 구간 안에 새로 시작할 충전 건수의 기대값 (정수 실적 아님) | 아래 4장 |
| new_sessions_p10 / p90 | 낮음·높음 수요 시나리오 | 기대값을 평균으로 한 포아송 분포의 10%·90% 분위 |
| duration_minutes | 급속·완속 충전 1건 시간 분포 (분) | 충북 세션 기록 최근 30일 |
| stations[].mean_duration_minutes | 그 충전소 평균 충전 시간 | 충전소 기록 5건 이상이면 충전소 평균(level=station), 아니면 시·군 평균(region), 그다음 충북 평균(all) |
| stations[].daily_starts | 하루 평균 충전 시작 건수 | 아래 4장 |
| validation | 실제 충북 시작 건수와의 비교 | 기록이 쌓이면 자동 채움 (급속 3시간, 완속 24시간 뒤부터) |

**API에 없어서 못 주는 것**: 충전량(kWh) — 시뮬레이션의 차량당 충전량은 계속 가정값입니다. 실제 도착·대기·포기 차량 — 충전을 시작한 건만 셉니다(`demand_measure: charging_starts`).

## 4. 신규 충전 건수 계산과 검증

```
하루 평균 시작률(건/시간) = 최근 14일 평균 충전 중 대수 ÷ 평균 충전 시간(시간)      ← 리틀의 법칙 L = λW
구간 기대 건수 = 하루 평균 시작률 × 시간대 모양(급속/완속, 평일/주말, 시각) × 0.25시간
```

- 시간대 모양은 서울 2025년 충전 18만 건의 시작 시각 분포입니다(`models/start_shape.json`, 하루 평균 1). 예: 급속 평일 새벽 3시 0.30배, 낮 12시 1.62배.
- 충북 세션 기록은 아직 충전기마다 마지막 1건만 있어 시작 시각이 저녁으로 쏠립니다. 그래서 모양은 서울 것을 씁니다. 빠짐없이 기록한 충북 세션이 2주 이상 쌓이면 충북 모양으로 바꿀 수 있습니다.
- 지금 순간의 충전 중 대수로 바로 나누지 않는 이유: 완속은 밤새 꽂아 둔 차 때문에 새벽에 충전 중 대수가 많지만, 새벽에 새로 시작하는 충전은 적습니다.

**서울 데이터로 검증** (`python -m scripts.eval_starts_seoul`, 결과 `models/start_method_eval.json`)
11월 마지막 14일로 시작률을 만들고 12월 한 달(186개 충전소 × 744시간)을 맞혔습니다.

| 방법 | 포아송 이탈도 | RMSE | 12월 합계 (실제 25,883건) |
|---|---|---|---|
| **이 방법 (14일 시작률 × 시간대 모양)** | **0.571** | **0.456** | 23,017 |
| 시간대 모양 없이 | 0.609 | 0.467 | 23,017 |
| 충전소·시각별 과거 평균 (1~11월) | 0.635 | 0.458 | 17,353 |

- 충전소별 한 달 건수 상관계수 0.98, 시각별 합계 오차 11%.
- 12월은 11월보다 수요가 늘어 합계는 11% 적게 나왔습니다. 최근 14일만 쓰기 때문에 1~11월 평균(33% 부족)보다 수준 변화를 훨씬 잘 따라갑니다.
- 충전소 1곳·1시간 단위는 건수가 0~2건이라 우연 변동이 큽니다. 시뮬레이션에서는 기대값과 p10·p90 시나리오를 함께 쓰는 것을 권합니다.
