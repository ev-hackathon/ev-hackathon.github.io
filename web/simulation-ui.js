(function () {
  'use strict';
  const sim = window.ESSimulation, config = JSON.parse(document.getElementById('sim-config').textContent);
  const $ = id => document.getElementById(id), escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const fmt = (n, d = 1) => Number.isFinite(n) ? n.toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
  const fields = [
    ['drivers', '신규 운전자 (명)', 24, 1, 500, 1], ['minutes', '도착 생성 기간 (분)', 60, 1, 1440, 1],
    ['discount', '여유 할인율 (%)', config.discount * 100, 0, 100, 1], ['surcharge', '혼잡 할증율 (%)', config.surcharge * 100, 0, 100, 1],
    ['energy', '차량당 충전량 (kWh)', 30, .1, 200, .1], ['sensitivity', '가격 민감도 (배)', 1, 0, 10, .1],
    ['alpha', '이동 비용 α (원/km)', 600, 0, 100000, 10], ['beta', '대기 비용 β (원/분)', 300, 0, 100000, 10],
    ['tau', '선택 다양성 τ (원)', 2000, 1, 100000, 1], ['threshold', '여유 판정 대기율 (%)', config.threshold * 100, 10, 90, 1],
    ['distance', 'B 추가 거리 (km)', 2, 0, 200, .001], ['repeats', '반복 실험 (회)', 100, 1, 500, 1], ['seed', '난수 시드', 42, 0, 4294967295, 1]
  ];
  let pool = [], last = null, busy = false, source = '예시 데이터', dataVersion = 0;
  $('simulation-view').innerHTML = `
    <span class="sim-kicker">ESSENTIAL · DEMAND LAB</span>
    <h2>요금이 바뀌면, 충전 수요는 어디로 갈까요?</h2>
    <p class="sim-lead">동일한 가상 운전자와 도착 조건에서 세 요금 정책을 비교합니다. 충전비, 이동거리, 대기시간을 함께 고려한 모의실험입니다.</p>
    <div class="sim-layout"><form id="sim-form" class="sim-card">
      <h3>01 · 실험 조건</h3><fieldset id="sim-controls" style="border:0;padding:0;margin:0;min-width:0"><div class="sim-fields">
        <label class="sim-wide">데이터 모드<select id="sim-mode"><option value="real">실제 공공데이터 · 마지막 수집 상태</option><option value="demo">예시 데이터 · 오프라인 데모</option></select></label>
        <label>분석 지역<select id="sim-region">${config.regions.map(r => `<option value="${escape(r.code)}">${escape(r.name)}</option>`).join('')}</select></label>
        <label>충전기 종류<select id="sim-speed"><option value="fast">급속</option><option value="slow">완속</option></select></label>
        <label class="sim-wide">충전소 A<select id="sim-a" required></select></label>
        <label class="sim-wide">충전소 B<select id="sim-b" required></select></label>
        ${fields.map(([id, label, value, min, max, step]) => `<label>${label}<input id="sim-${id}" type="number" value="${value}" min="${min}" max="${max}" step="${step}" required></label>`).join('')}
        <label class="sim-wide">추가 거리 산정<select id="sim-distanceMode"><option value="coords">A↔B 좌표 직선거리 (A: 0km)</option><option value="manual">사용자 가정값 (A: 0km, B: 입력값)</option></select></label>
        <label class="sim-wide">신규 차량 충전시간<select id="sim-durationMode"><option value="assumed">20~40분 균등분포 (간소화 가정)</option><option value="power">충전량 ÷ 대표 출력 × 60분</option></select></label>
        <label class="sim-wide">요금 판정 기준<select id="sim-pricingBasis"><option value="observed">현재 수집 상태</option><option value="forecast">외부 AI 모델 예측 이용률</option></select></label>
      </div>
      <div id="sim-forecast-panel" hidden>
        <p class="sim-note">모델이 만든 JSON을 파일·URL·붙여넣기로 입력하세요. A/B의 동일한 종류 예측이 모두 필요합니다. 예측 이용률은 요금에만 적용하며 초기 대기열은 실제 수집 상태를 유지합니다.</p>
        <label class="sim-note">예측 JSON 파일<input type="file" id="sim-forecast-file" accept=".json,application/json"></label>
        <label class="sim-note" style="display:block;margin-top:10px">예측 JSON URL<input id="sim-forecast-url" type="text" value="data/forecast.json" style="width:100%;box-sizing:border-box"></label>
        <button type="button" id="sim-forecast-fetch">URL에서 예측 불러오기</button>
        <label class="sim-note" style="display:block;margin-top:10px">예측 JSON<textarea id="sim-forecast-json" rows="9" style="width:100%;box-sizing:border-box" placeholder="AI 모델의 실제 출력 JSON을 입력하세요"></textarea></label>
        <p class="sim-note" id="sim-forecast-status" role="status">예측 데이터가 입력되지 않았습니다. 임의 예측값은 생성하지 않습니다.</p>
      </div></fieldset>
      <div class="sim-actions"><button class="sim-primary" id="sim-run" type="submit">시뮬레이션 실행</button><button id="sim-reload" type="button">데이터 새로 읽기</button></div>
      <p class="sim-note">가격 민감도 1배에서 C = E×P + α×D + β×W. 민감도는 E×P 항에 곱합니다. 모든 행동계수는 실측값이 아닌 가정입니다. 시간대 배수는 적용하지 않습니다.</p>
    </form><div>
      <div class="sim-card"><h3>02 · 비교할 두 충전소</h3><span class="sim-badge" id="sim-source"></span><div id="sim-message" class="sim-status" role="status" aria-live="polite"></div><div id="sim-stations" class="sim-stations"></div></div>
      <progress id="sim-progress" max="1" value="0" hidden aria-label="시뮬레이션 진행률"></progress>
      <div id="sim-results" aria-live="polite"><div class="sim-card"><h3>03 · 정책 비교</h3><p class="sim-note">기본 예시 조건으로 바로 실행할 수 있습니다. 결과는 실제 수요 분산 실적 또는 배전망 피크 저감 성과가 아닙니다.</p></div></div>
      <details class="sim-card"><summary>모델 가정과 결과 읽는 방법</summary>
        <p>운전자의 도착 시각은 입력한 기간 내 균등분포로 생성합니다. A를 원래 목적지로 가정해 추가 거리 0km, B는 입력값 또는 충전소 간 직선거리를 사용합니다. 이동 소요시간은 따로 더하지 않으며 선택과 충전소 도착은 같은 시각에 처리합니다.</p>
        <p>초기 충전 차량의 잔여시간은 0~30분 균등분포입니다. 출력 모드에서는 0~(충전량÷대표 출력×60)분입니다. 신규 충전시간은 20~40분 가정 또는 출력 계산을 사용합니다. 충전효율·배터리 충전곡선·출력 공유는 반영하지 않습니다. 혼합 출력 충전소는 기존 관제의 대표 출력·단가를 동일하게 적용합니다.</p>
        <p>판정 분모는 빈 충전기+충전 중이며 고장·미확인 충전기는 처리 능력에서 제외합니다. 요금 기준은 현재 상태 또는 외부 예측 이용률로 선택합니다. 예측 모드에서도 초기 점유는 실제 수집 상태이며 요금만 예측값으로 고정합니다. 예측 대상 미래 시점의 대기열을 복원하는 기능은 없습니다. AI 예측값을 임의 생성하지 않습니다.</p>
        <p>선택 당시 예상 대기는 이미 도착한 차량을 포함한 가장 빠른 충전기 사용 가능 시각으로 계산합니다. 실제 대기는 도착순 FIFO 예약 후 계산합니다. 같은 정보와 변경 없는 대기열을 사용하는 이 모델에서는 두 값이 일치합니다. 기간 내 도착한 모든 신규 차량을 충전 완료까지 집계하며 초기 차량의 요금은 제외합니다.</p>
        <p>결과는 반복 1회당 평균입니다. 평균 대기의 표준오차(SE)는 반복 실험 간 변동이며 실세계 예측 오차가 아닙니다. 총 할인 혜택은 기본 단가 대비 할인액의 합으로 사업자 순손실과 다릅니다. 수입 차이는 정책별 선택 충전소와 단가로 계산한 단순 매출 차이이며 비용을 반영하지 않습니다.</p>
      </details>
    </div></div>`;
  function message(text, error = false) { $('sim-message').textContent = text; $('sim-message').dataset.error = error; }
  function selected() { return [$('sim-a').value, $('sim-b').value].map(id => pool.find(s => s.id === id)); }
  function invalidate() {
    if (last) $('sim-export').disabled = true;
    $('sim-results').dataset.stale = 'true';
    if (last) $('sim-result-state').textContent = '조건이 변경되었습니다. 아래는 이전 실행 결과입니다. 다시 실행해 주세요.';
  }
  function distance() {
    const [a, b] = selected();
    const coords = $('sim-distanceMode').value === 'coords'; $('sim-distance').readOnly = coords;
    if (coords && a && b) {
      const rad = Math.PI / 180, h = Math.sin((b.lat - a.lat) * rad / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lng - a.lng) * rad / 2) ** 2;
      $('sim-distance').value = (6371 * 2 * Math.asin(Math.sqrt(Math.min(1, h)))).toFixed(3);
    }
  }
  function cards() {
    distance(); $('sim-source').textContent = source;
    $('sim-stations').innerHTML = selected().map((s, i) => !s ? '<p>선택할 충전소가 없습니다.</p>' : `<div class="sim-station"><strong>${i ? 'B' : 'A'} · ${escape(s.name)}</strong>
      <div class="sim-note">ID ${escape(s.id)} · ${s.speed === 'fast' ? '급속' : '완속'}<br>${fmt(s.lat, 5)}, ${fmt(s.lng, 5)}<br>
      빈 충전기 ${s.avail}대 · 충전 중 ${s.charging}대<br>사용 불가 ${s.fault}대 · 미확인 ${s.unknown || 0}대<br>처리 가능 ${s.avail + s.charging}대 · 현재 ${sim.status(s, +$('sim-threshold').value / 100)}<br>
      기본 ${fmt(s.base)}원/kWh · ${fmt(s.kw)}kW<br>${escape(s.source)}</div></div>`).join('');
  }
  function choosePool(items) {
    pool = items.filter(s => s.avail + s.charging > 0 && Number.isFinite(s.base) && s.base > 0 && s.kw > 0 && Number.isFinite(s.lat) && Number.isFinite(s.lng));
    const options = pool.map(s => `<option value="${escape(s.id)}">${escape(s.name)} · ${sim.status(s, +$('sim-threshold').value / 100)} · ${escape(s.id)}</option>`).join('');
    $('sim-a').innerHTML = options; $('sim-b').innerHTML = options;
    const threshold = +$('sim-threshold').value / 100;
    const a = pool.find(s => sim.status(s, threshold) === '혼잡') || pool[0];
    const candidates = pool.filter(s => s !== a && sim.status(s, threshold) === '여유');
    candidates.sort((x, y) => (x.lat - a.lat) ** 2 + (x.lng - a.lng) ** 2 - (y.lat - a.lat) ** 2 - (y.lng - a.lng) ** 2);
    const b = candidates[0] || pool.find(s => s !== a);
    if (a) $('sim-a').value = a.id; if (b) $('sim-b').value = b.id;
    cards(); $('sim-run').disabled = pool.length < 2;
    return a && sim.status(a, threshold) === '혼잡' && candidates.length > 0;
  }
  async function load() {
    const version = ++dataVersion; invalidate();
    $('sim-run').disabled = true; $('sim-reload').disabled = true;
    try {
      const demo = $('sim-mode').value === 'demo', speed = $('sim-speed').value;
      $('sim-region').disabled = demo;
      if (demo) {
        source = '예시 데이터 · 실제 충전소가 아닙니다';
        choosePool(sim.demo().map(s => ({ ...s, speed, kw: speed === 'fast' ? 60 : 7 })));
        message('A: 4대 모두 충전 중 / B: 4대 중 1대 충전 중. 지역 선택은 실제 데이터 모드에서 적용합니다. 예시 기본 단가는 양쪽 347.2원/kWh입니다.');
      } else {
        message('실제 충전소 데이터를 읽는 중입니다…');
        source = '실제 데이터 조회 중'; pool = []; cards();
        if (!window.ESSMonitor) throw new Error('관제 데이터 연결을 초기화하지 못했습니다. 지도 라이브러리 연결을 확인하거나 예시 모드를 사용하세요.');
        await window.ESSMonitor.ready;
        if (version !== dataVersion) return;
        const data = await window.ESSMonitor.stations($('sim-region').value);
        if (version !== dataVersion) return;
        source = `실제 수집 데이터 · ${data.when || '수집 시각 미제공'} · ${data.kind === 'snapshot' ? '저장본' : '마지막 수집 상태'}`;
        const pair = choosePool(data.list.map(s => ({ id: s.id, name: s.name, lat: s.lat, lng: s.lng, speed,
          ...s[speed], unknown: s[speed].total - s[speed].avail - s[speed].charging - s[speed].fault, source: s[speed].src || '기존 운영사 요금표' })));
        message(pool.length < 2 ? '동일 종류의 처리 가능한 충전소가 2곳 미만입니다. 예시 데이터 모드로 전환해 주세요.' : pair ? '같은 종류의 혼잡 A와 가장 가까운 여유 B를 선택했습니다. 다른 충전소도 직접 선택할 수 있습니다. 거리는 별도 설정을 확인하세요.' : '혼잡 A·여유 B 조건에 맞는 쌍이 없습니다. 비교 가능한 두 거점을 표시했습니다. 예시 데이터 모드도 사용할 수 있습니다.');
      }
    } catch (error) {
      if (version === dataVersion) { source = '실제 데이터 없음'; choosePool([]); message(error.message + ' 예시 데이터 모드를 선택하면 데모를 실행할 수 있습니다.', true); }
    } finally { if (version === dataVersion) $('sim-reload').disabled = false; }
  }
  function options() {
    const o = Object.fromEntries(fields.map(([key]) => [key, +$('sim-' + key).value]));
    for (const k of ['discount', 'surcharge', 'threshold']) o[k] /= 100;
    o.durationMode = $('sim-durationMode').value;
    return o;
  }
  function render(result) {
    const { results, options: o } = result, maxWait = Math.max(1, ...results.map(r => r.wait));
    const maxDiscount = Math.max(1, ...results.map(r => r.discount));
    const bar = (r, value, max, unit) => `<div class="sim-bar-row"><div class="sim-bar-label"><span>${r.label}</span><b>${fmt(value)}${unit}</b></div><div class="sim-track"><div class="sim-fill sim-wait" style="width:${value / max * 100}%"></div></div></div>`;
    $('sim-results').innerHTML = `<div class="sim-card"><h3>03 · 세 정책, 같은 운전자</h3>
      <p class="sim-note" id="sim-result-state">${escape(result.context.source)} · ${escape(result.context.region)} · 가상 시뮬레이션 결과<br>요금 기준: ${escape(result.context.pricingBasis)}<br>${escape(result.context.forecastInfo || '')}<br>반복 ${o.repeats}회 평균 · 신규 ${o.drivers}명 · 도착 ${o.minutes}분 · ${o.energy}kWh · 시드 ${o.seed} · B 추가 ${fmt(o.distance, 3)}km</p>
      <div class="sim-table-wrap"><table class="sim-table"><caption>정책별 비교 (반복 1회당 평균)</caption><thead><tr><th>지표</th>${results.map(r => `<th scope="col">${r.label}</th>`).join('')}</tr></thead><tbody>
      ${[
        ['A 적용 단가 (원/kWh)', r => fmt(r.prices[0])], ['B 적용 단가 (원/kWh)', r => fmt(r.prices[1])],
        ['A 선택 (명 / %)', r => `${fmt(r.count[0])} / ${fmt(r.share[0] * 100)}%`], ['B 선택 (명 / %)', r => `${fmt(r.count[1])} / ${fmt(r.share[1] * 100)}%`],
        ['전체 평균 실제 대기 (분)', r => fmt(r.wait, 2)], ['선택 당시 예상 대기 (분)', r => fmt(r.expectedWait, 2)], ['평균 대기 표준오차 (분)', r => fmt(r.waitSE, 2)],
        ['고정 대비 대기 변화 (%)', r => r.waitChange === null ? '산정 불가 (기준 0분)' : fmt(r.waitChange)], ['평균 충전요금 (원/명)', r => fmt(r.fee)],
        ['평균 추가 거리 (km/명)', r => fmt(r.distance, 3)], ['총 할인 혜택 (원)', r => fmt(r.discount)], ['요금 수입 (원)', r => fmt(r.revenue)],
        ['고정 대비 수입 차이 (원)', r => fmt(r.revenueChange)], ['마지막 충전 완료 (시작 후 분)', r => fmt(r.completion)]
      ].map(([label, get]) => `<tr><th scope="row">${label}</th>${results.map(r => `<td>${get(r)}</td>`).join('')}</tr>`).join('')}
      </tbody></table></div><div class="sim-actions"><button id="sim-export" type="button">결과 CSV 다운로드</button></div></div>
      <div class="sim-charts"><div class="sim-card"><h3>충전소 선택 인원</h3><p class="sim-legend"><span></span>A &nbsp; <span></span>B · 명/회</p>${results.map(r => `<div class="sim-bar-row"><div class="sim-bar-label"><span>${r.label}</span><b>A ${fmt(r.count[0])} / B ${fmt(r.count[1])}명</b></div><div class="sim-track" role="img" aria-label="${r.label}: A ${fmt(r.count[0])}명, B ${fmt(r.count[1])}명"><div class="sim-fill" style="width:${r.share[0] * 100}%"></div><div class="sim-fill-b" style="width:${r.share[1] * 100}%"></div></div></div>`).join('')}</div>
      <div class="sim-card"><h3>평균 실제 대기시간</h3>${results.map(r => bar(r, r.wait, maxWait, '분')).join('')}</div></div>
      <div class="sim-card"><h3>이용자 총 할인 혜택</h3>${results.map(r => bar(r, r.discount, maxDiscount, '원')).join('')}<p class="sim-note">기본 단가 대비 할인 혜택의 합입니다. 사업자의 순손실을 의미하지 않습니다. 할증에 따른 추가 부담은 할인액에서 차감하지 않습니다.</p></div>`;
    $('sim-export').addEventListener('click', exportCSV);
  }
  function exportCSV() {
    if (!last || $('sim-export').disabled) return;
    const { options: o, stations, results, context } = last;
    const rows = [['항목', '값'], ['데이터 출처', context.source], ['지역', context.region], ['결과 구분', '가상 시뮬레이션 · 회당 평균'],
      ...Object.entries(o), ...Object.entries(context), ...stations.flatMap((s, i) => Object.entries(s).map(([k, v]) => [`${i ? 'B' : 'A'}_${k}`, v])), [],
      ['정책', 'A단가_원kWh', 'B단가_원kWh', 'A선택_명', 'B선택_명', 'A비율', 'B비율', '실제대기_분', '예상대기_분', '대기표준오차_분', '고정대비대기변화_pct', '평균요금_원', '평균추가거리_km', '총할인혜택_원', '수입_원', '고정대비수입차이_원', '마지막완료_분'],
      ...results.map(r => [r.label, ...r.prices, ...r.count, ...r.share, r.wait, r.expectedWait, r.waitSE, r.waitChange, r.fee, r.distance, r.discount, r.revenue, r.revenueChange, r.completion])];
    const cell = value => { const text = String(value ?? ''); return '"' + (/^[=+@-]/.test(text) && !Number.isFinite(Number(text)) ? "'" : '') + text.replace(/"/g, '""') + '"'; };
    const blob = new Blob(['\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `essential-simulation-${o.seed}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('sim-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !$('sim-form').reportValidity()) return;
    const stations = selected();
    if (stations.some(s => !s)) return message('같은 종류의 충전소 2곳을 선택하세요.', true);
    busy = true; $('sim-controls').disabled = true; $('sim-run').disabled = true; $('sim-reload').disabled = true; $('sim-progress').hidden = false;
    message('세 정책에 동일한 운전자를 적용해 계산하고 있습니다…');
    try {
      let prediction = null;
      if ($('sim-pricingBasis').value === 'forecast') {
        prediction = readForecast();
        $('sim-forecast-status').textContent = `${prediction.model} · 대상 ${prediction.targetTime} · A ${(prediction.utilizationById[stations[0].id] * 100).toFixed(1)}%, B ${(prediction.utilizationById[stations[1].id] * 100).toFixed(1)}%`;
      }
      const result = await sim.compare(stations, options(), p => { $('sim-progress').value = p; }, prediction?.utilizationById);
      result.context = { source, region: $('sim-mode').value === 'demo' ? '예시 · 지역 무관' : $('sim-region').selectedOptions[0].text,
        mode: $('sim-mode').value, speed: $('sim-speed').value, distanceMode: $('sim-distanceMode').value,
        pricingBasis: prediction ? '외부 모델의 예측 이용률 (요금에만 적용)' : '현재 수집 상태',
        forecastInfo: prediction ? `${prediction.model} · 생성 ${prediction.generatedAt} · 대상 ${prediction.targetTime}` : '',
        forecastUtilizationA: prediction?.utilizationById[stations[0].id] ?? '', forecastUtilizationB: prediction?.utilizationById[stations[1].id] ?? '' };
      last = result; render(result); message('계산 완료. 아래 결과는 가상 운전자 모의실험이며 실제 관측 성과가 아닙니다.');
    } catch (error) { message(error.message, true); }
    finally { busy = false; $('sim-controls').disabled = false; $('sim-region').disabled = $('sim-mode').value === 'demo'; $('sim-run').disabled = pool.length < 2; $('sim-reload').disabled = false; $('sim-progress').hidden = true; }
  });
  $('sim-form').addEventListener('input', invalidate);
  function readForecast() {
    const stations = selected();
    if (stations.some(s => !s)) throw new Error('먼저 충전소 A/B를 선택하세요.');
    let payload;
    try { payload = JSON.parse($('sim-forecast-json').value); } catch { throw new Error('예측 JSON 형식을 확인하세요.'); }
    return sim.forecast(payload, stations);
  }
  function checkForecast() {
    invalidate();
    try {
      const p = readForecast();
      $('sim-forecast-status').textContent = `검증 완료 · ${p.model} · 생성 ${p.generatedAt} · 대상 ${p.targetTime}. 실행 버튼을 눌러 적용하세요.`;
    } catch (error) { $('sim-forecast-status').textContent = error.message; }
  }
  $('sim-pricingBasis').addEventListener('change', () => {
    invalidate(); $('sim-forecast-panel').hidden = $('sim-pricingBasis').value !== 'forecast';
    if (!$('sim-forecast-panel').hidden) checkForecast();
  });
  $('sim-forecast-json').addEventListener('input', checkForecast);
  $('sim-forecast-file').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file) return;
    if (file.size > 2000000) { $('sim-forecast-status').textContent = '2MB 이하 JSON 파일을 선택하세요.'; return; }
    $('sim-forecast-json').value = await file.text(); checkForecast();
  });
  $('sim-forecast-fetch').addEventListener('click', async () => {
    const button = $('sim-forecast-fetch'); button.disabled = true;
    // Clear previous input so a failed fetch cannot silently reuse another forecast.
    $('sim-forecast-json').value = ''; invalidate();
    $('sim-forecast-status').textContent = '예측 JSON을 읽는 중입니다…';
    try {
      const url = new URL($('sim-forecast-url').value, location.href);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('인증정보·쿼리·해시 없는 HTTP(S) JSON URL을 사용하세요.');
      const response = await fetch(url, { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error(`예측 파일 응답 ${response.status}`);
      const body = await response.text();
      if (body.length > 2000000) throw new Error('2MB 이하 JSON만 사용할 수 있습니다.');
      $('sim-forecast-json').value = body; checkForecast();
    } catch (error) { $('sim-forecast-status').textContent = `${error.message} · 외부 URL은 CORS 허용이 필요합니다.`; }
    finally { button.disabled = false; }
  });
  $('sim-form').addEventListener('change', invalidate);
  for (const id of ['mode', 'region', 'speed']) $('sim-' + id).addEventListener('change', load);
  for (const id of ['a', 'b', 'distanceMode', 'threshold']) $('sim-' + id).addEventListener('change', cards);
  $('sim-reload').addEventListener('click', load);
  for (const [id, active] of [['tab-monitor', false], ['tab-simulation', true]]) $(id).addEventListener('click', () => {
    $('monitor-view').hidden = active; $('simulation-view').hidden = !active;
    $('tab-monitor').setAttribute('aria-pressed', String(!active)); $('tab-simulation').setAttribute('aria-pressed', String(active));
    $('regions').hidden = active;
    if (!active) window.ESSMonitor?.resize();
  });
  if (location.hash === '#simulation') $('tab-simulation').click();
  load();
})();
