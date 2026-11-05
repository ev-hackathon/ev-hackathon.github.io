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
| 충전 1건당 충전량 | 한국환경공단 기후에너지환경부 공공급속 충전기 충전정보 (월별 실적) | 월 1회 (2025-08까지) |
| 운영사 회원가 | 무공해차 통합누리집 운영사별 충전요금 | 사이트 생성 시 |
| 기온·강수량·습도 | 기상청 ASOS(학습), 초단기예보(예측) | 1시간 |
| 서울시 충전 이력 2025년 1년치 | 서울 열린데이터광장 — 이용률 모델의 사전학습 구성요소, 신규 건수 계산 방법의 검증용 (신규 건수 값 자체는 충북 데이터만 사용) | 고정 |

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
                "duration_level": "station", "mean_charging": 1.623, "daily_starts": 82.4,
                "daily_starts_formula": 101.2, "occupancy_k": 1.27, "counted_starts": 31,
                "counted_hours_shape_weighted": 19.4, "mean_energy_kwh": 24.1, "energy_source": "충전소 실측"}],
  "energy_kwh": {"fast": {"kwh_per_session": 23.38, "quantiles": {"p10": 16.9, "p50": 23.7, "p90": 29.2}},
                 "by_capacity_kw": {"50": {"kwh_per_session": 16.7}, "100": {"kwh_per_session": 23.48}, "...": "..."},
                 "slow": {"kwh_per_session": null, "note": "완속은 실측 자료 없음 ..."}},
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
| stations[].daily_starts | 하루 평균 충전 시작 건수 (실제 쓰는 값) | 아래 4장 |
| stations[].daily_starts_formula | 보정 전 공식 값 (평균 충전 중 대수 ÷ 충전 시간) | 비교용 |
| stations[].occupancy_k | 점유 보정 배수 | 아래 4장 |
| stations[].counted_starts | 빠짐없이 기록된 시간대에 실제로 센 시작 건수 | 세션 시각을 주지 않는 운영사 충전소는 0 |
| stations[].mean_energy_kwh, energy_source | 충전 1건당 충전량(kWh)과 근거 | 급속: 그 충전소 실측("충전소 실측") 또는 충북 공공급속 평균. 완속: 없음 |
| energy_kwh | 충북 공공급속 1건당 충전량 요약 (전체, 출력별, 분위) | 환경부 공공급속 충전기 월별 실적, 최근 12개월 |
| validation | 실제 충북 시작 건수와의 비교 (구간별 by_window 포함) | 빠짐없이 기록된 시간대만, 시간 순서대로 (급속 3시간, 완속 12시간 지난 뒤부터) |

**API에 없어서 못 주는 것**: 완속 충전량(kWh) — 급속은 환경부 공공급속 실측을 씁니다. 실제 도착·대기·포기 차량 — 충전을 시작한 건만 셉니다(`demand_measure: charging_starts`).

## 4. 신규 충전 건수 계산과 검증 (충북 데이터 기준)

```
공식 시작률(건/시간) = 최근 14일 평균 충전 중 대수 ÷ (점유 보정 k × 평균 충전 시간)      ← 리틀의 법칙 L = λW
하루 평균 시작률     = (실제로 센 시작 건수 N + 24 × 공식 시작률) ÷ (센 시간 E + 24)     ← 실측과 공식을 섞음
구간 기대 건수       = 하루 평균 시작률 × 시간대 모양(급속/완속, 15분 칸) × 0.25시간
```

- **평균 충전 중 대수**: 충북 수집 기록. **평균 충전 시간**: 충북 충전 세션 기록.
- **점유 보정 k**: 충전이 끝나도 차를 꽂아 둔 동안 상태가 '충전 중'으로 남습니다. 11월 4일 낮(8~14시) 상태 기준 충전 시간이 세션 기록보다 급속 1.24배, 완속 1.38배 길었고, 밤을 포함한 전체 촘촘한 기록 구간에서는 급속 1.30배, 완속 1.80배였습니다(완속은 밤새 꽂아 두는 차가 많음). 보정 없이 나누면 시작 건수가 30~46% 부풀어서, 그 비율만큼 충전 시간을 늘립니다. k는 매번 그 시점 이전의 촘촘한 기록으로 다시 재며, 값은 `stations[].occupancy_k`에 실립니다.
- **실측 섞기**: 세션이 빠짐없이 기록된 시간대에 그 충전소에서 실제로 시작한 건수 N을 셉니다. E는 그 시간들의 시간대 모양 합입니다. 공식 값을 '하루(24시간)치 관측'만큼 믿고 섞으므로(포아송-감마), 기록이 쌓일수록 실측 쪽으로 옮겨 갑니다. 세션 시각을 한 번도 주지 않은 운영사 충전소는 셀 수 없어 공식만 씁니다.
- **시간대 모양**: 충북 전 충전소의 수집 시각별 평균 충전 중 대수(3주)를 역합성곱해서 구합니다. 어느 시각에 충전 중인 차는 그 전에 시작해서 아직 안 끝난 차들의 합이므로, 충북 충전 시간 분포를 알면 시각별 시작 건수를 거꾸로 풀 수 있습니다. 매 갱신 때 최신 충북 기록으로 다시 계산하고 `start_shape_hourly`에 시각별 값을 싣습니다.
- 충북 결과(하루 평균 1):
  - 급속: 새벽 3시 0.26배, 낮 12~13시 1.72배.
  - 완속: 저녁 19~20시 1.45~1.47배로 가장 높습니다. 아파트 등에서 퇴근 뒤 꽂는 충전이 많다는 뜻입니다.
- 서울 시설 충전기의 완속은 아침 8시가 최고(2.37배)라 충북과 전혀 다릅니다(상관 0.12). 그래서 서울 분포를 그대로 쓰지 않습니다. 급속은 서울과 비슷합니다(상관 0.98).
- 시간대 모양을 세션으로 직접 세지 않는 이유: 15분 간격 기록 전의 세션은 충전기마다 마지막 1건이라 시작 시각이 저녁으로 쏠립니다. 다만 아래 충북 검증에서 역합성곱 모양이 실제 시각별 합계와 잘 맞았습니다(06시 143 vs 146건, 12시 378 vs 402건, 21시 155 vs 159건).
- 지금 순간의 충전 중 대수로 바로 나누지 않는 이유: 완속은 밤새 꽂아 둔 차 때문에 새벽에 충전 중 대수가 많지만, 새벽에 새로 시작하는 충전은 적습니다.
- 평일/주말은 아직 주말 수집이 적어 나누지 않았습니다.

**방법 검증 — 정답을 아는 서울 데이터에 똑같이 적용** (`python -m scripts.eval_starts_seoul`, 결과 `models/start_method_eval.json`)

충북과 똑같이 만들었습니다. 시작률은 11월 마지막 14일로 구했고, 시간대 모양은 띄엄띄엄한 '충전 중 대수' 스냅숏 160개만으로 역합성곱했습니다. 이걸로 12월 한 달(186개 충전소 × 744시간)의 1시간 단위 시작 건수를 맞혔습니다.

| 방법 | 포아송 이탈도 | RMSE | 12월 합계 (실제 25,883건) |
|---|---|---|---|
| **이 방법 (14일 시작률 × 역합성곱 모양)** | **0.573** | **0.457** | 23,017 |
| 실제 시작 시각 분포를 알았다면 | 0.571 | 0.456 | 23,017 |
| 시간대 모양 없이 | 0.609 | 0.467 | 23,017 |
| 충전소·시각별 과거 평균 (1~11월) | 0.635 | 0.458 | 17,353 |

- 역합성곱으로 복원한 시간대 모양과 실제 시작 분포의 상관은 0.98입니다. 실제 시작 시각을 알았을 때와 거의 같은 정확도입니다.
- 급속·완속을 나눠 같은 시험(스냅숏 160개)을 하면 복원 상관이 급속 0.99, 완속 0.80~0.93입니다. 완속은 충전이 길어 풀기가 더 어렵습니다.
- 충전소별 한 달 건수 상관계수는 0.98입니다. 12월은 수요가 늘어 합계가 11% 적게 나왔습니다. 최근 14일만 쓰기 때문에 1~11월 평균(33% 부족)보다 수준 변화를 훨씬 잘 따라갑니다.
- 충전소 1곳·1시간 단위는 건수가 0~2건이라 우연 변동이 큽니다. 시뮬레이션에서는 기대값과 p10·p90 시나리오를 함께 쓰는 것을 권합니다.
**충북 실측 검증** (`validation`, 매 갱신 때 자동 계산)

빠짐없이 기록된 구간마다 그 구간 시작 이전 자료로만 추정을 만들고, 그 구간의 충전소별 1시간 시작 건수에 맞혀 봅니다. 2026-11-05 새벽 기준 급속 결과입니다.

| 구간 | 실제 시작 | 보정·실측 섞기 | 공식만 |
|---|---|---|---|
| 11.4 06~13시 (이전 촘촘한 기록 없음 → 공식과 같음) | 1,917건 | 2,492건 (1.30배) | 2,492건 |
| 11.4 20~21시 (11.4 낮 기록으로 보정) | 288건 | **322건 (1.12배)**, RMSE 0.364 | 421건 (1.46배), RMSE 0.389 |

- 촘촘한 기록이 쌓일수록 보정과 실측 섞기가 들어가는 구간이 늘어납니다. 최신 결과는 `validation.fast.by_window`에 있습니다.
- 완속은 충전이 길어, 끊김 없이 13시간 이상 기록된 뒤부터 검증합니다.
- 실측 건수만 쓰면(공식을 섞지 않으면) 포아송 이탈도가 0.45에서 0.95로 나빠져, 충전소별 하루치 기록만으로는 부족하다는 점도 확인했습니다.

## 5. 충전 1건당 충전량 (kWh)

환경부 공공급속 충전기 충북 월별 실적(누적 충전량 ÷ 충전 횟수, 2024.9~2025.8, 53.9만 건, `data/energy_fast_43.json`, `python -m scripts.energy_summary`):

| | 1건당 충전량 |
|---|---|
| 급속 전체 | **23.4kWh** (p10 16.9, 중앙 23.7, p90 29.2) |
| 50kW | 16.7kWh |
| 100kW | 23.5kWh |
| 200kW | 25.1kWh |
| 300kW | 25.4kWh |
| 350kW | 31.7kWh |

- 월별 22.5~24.4kWh로 계절 차이가 작습니다.
- 충전건별 충전량 합성데이터(가상 자료) 충북 표본도 급속 평균 22.6kWh로 비슷합니다. 완속은 실측이 없고 합성데이터 참고값만 있습니다(평균 15.8kWh, 291건).
- 서울 2025 충전 이력에서 '충전 시간 → 충전량' 관계를 배워 충북 급속 세션 시간에 적용해도 중앙값 22.8kWh로 거의 같았습니다.
- 환경부 공공급속 충전기만이라 민간 운영사 충전기와는 다를 수 있습니다. 우리 급속 충전소 중 307곳이 이 자료와 일치해 충전소별 값을 씁니다.

## 6. 충전 세션 원자료 — `data/sessions/{시·군 코드}.csv`

끝난 충전 세션(충전기별 시작·종료 시각)을 시·군별 CSV 하나로 공개합니다. 30분마다 갱신되고, 끝난 날짜 기준 최근 30일치입니다.

| 파일 | 주소 |
|---|---|
| 시·군별 세션 | `https://ev-hackathon.github.io/data/sessions/43110.csv` (청주시. 코드는 `config.REGIONS`) |
| 목록 | `https://ev-hackathon.github.io/data/sessions/index.json` (시·군별 행 수, 원본 파일 목록, 기록 커밋) |

- 열: `statId,chgerId,speed,kw,start,end,minutes` — history 브랜치 `history/sessions/{날짜}/{코드}.csv`와 똑같습니다.
- GitHub API(`api.github.com`)는 로그인 없이 쓰면 **접속한 곳(IP)마다 1시간에 60번**까지만 됩니다. 발표장처럼 같은 와이파이에서 여러 명이 열면 막힐 수 있으니, 브라우저에서 세션 자료를 읽을 때는 이 주소를 쓰세요(호출 제한 없음, CORS 허용).
- 2026-11-04 이전 기록은 충전기마다 마지막 1건만 있습니다.

B조 `web/session-timing.js`의 `createClient` 안 `load` 함수를 아래로 바꾸면 GitHub API 없이 같은 자료를 읽습니다 (`index()`는 더 쓰지 않음, `plan`·`csv`는 그대로):

```js
async function load(stations, region, asOf = now()) {
  if (!/^\d{5}$/.test(region)) throw new Error('세션 분석 지역을 확인하세요.');
  const base = 'https://ev-hackathon.github.io/data/sessions/';
  const key = region + ':' + Math.floor(asOf / 900000);          // 15분마다 새로 받음
  if (!regions.has(key)) regions.set(key, (async () => {
    const idx = await json(base + 'index.json');
    const r = await fetcher(base + region + '.csv', { credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(20000) });
    if (!r.ok) throw new Error(`세션 파일 응답 ${r.status}`);
    return { rows: csv(await r.text()), idx };
  })().catch(e => { regions.delete(key); throw e; }));
  const data = await regions.get(key);
  return plan(data.rows, stations, region, asOf, { repository: base, commit: data.idx.history_commit,
    files: data.idx.regions[region]?.files || [], fetchedAt: new Date(now()).toISOString() });
}
```
