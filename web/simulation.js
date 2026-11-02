/* Browser + Node pure simulation engine. No network, credentials, or UI dependencies. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ESSimulation = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const policies = ['고정요금', '할인·할증', '할인 전용'];
  const defaults = { drivers: 24, minutes: 60, energy: 30, discount: .2, surcharge: .15,
    alpha: 600, beta: 300, tau: 2000, sensitivity: 1, repeats: 100, seed: 42,
    distance: 2, threshold: .5, durationMode: 'assumed' };
  const demo = () => [
    { id: 'DEMO-A', name: '예시 A · 혼잡 거점', lat: 36.6424, lng: 127.489, avail: 0, charging: 4, fault: 0, base: 347.2, kw: 60, speed: 'fast', source: '예시 단가' },
    { id: 'DEMO-B', name: '예시 B · 여유 거점', lat: 36.6604, lng: 127.489, avail: 3, charging: 1, fault: 0, base: 347.2, kw: 60, speed: 'fast', source: '예시 단가' }
  ];
  function rng(seed) {
    let a = seed >>> 0;
    return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ t >>> 15, t | 1);
      t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  function status(s, threshold = .5) {
    const usable = s.avail + s.charging;
    return !usable ? (s.fault ? '불가' : '미확인') : s.avail / usable >= threshold ? '여유' : s.avail > 0 ? '보통' : '혼잡';
  }
  function forecast(payload, stations, now = Date.now()) {
    if (!payload || payload.schema_version !== 1 || typeof payload.model_name !== 'string' || !payload.model_name.trim() || !Array.isArray(payload.predictions))
      throw new Error('예측 JSON의 schema_version(1), model_name, predictions를 확인하세요.');
    const timestamp = value => typeof value === 'string' && /(Z|[+-]\d\d:\d\d)$/.test(value) ? Date.parse(value) : NaN;
    const generated = timestamp(payload.generated_at), target = timestamp(payload.target_time);
    if (!Number.isFinite(generated) || !Number.isFinite(target) || generated > now + 300000 || target <= generated || target <= now)
      throw new Error('예측 생성·대상 시각을 확인하세요. 시간대가 필요하며, 지난 대상 시각은 사용할 수 없습니다.');
    const entries = new Map();
    for (const row of payload.predictions) {
      if (!row || typeof row.station_id !== 'string' || !['fast', 'slow'].includes(row.speed) || !Number.isFinite(row.utilization) || row.utilization < 0 || row.utilization > 1)
        throw new Error('예측에는 station_id, speed(fast/slow), utilization(0~1)이 필요합니다.');
      const key = row.station_id + ':' + row.speed;
      if (entries.has(key)) throw new Error('중복된 충전소·종류 예측이 있습니다.');
      entries.set(key, row.utilization);
    }
    const utilizationById = Object.create(null);
    for (const s of stations) {
      const key = s.id + ':' + s.speed;
      if (!entries.has(key)) throw new Error(`${s.id} (${s.speed})의 예측값이 없습니다. A/B 모두 필요합니다.`);
      utilizationById[s.id] = entries.get(key);
    }
    return { utilizationById, model: payload.model_name, generatedAt: payload.generated_at, targetTime: payload.target_time };
  }
  // Optional utilizationById contains externally supplied future utilization [0,1]. Never generated here.
  function price(s, policy, o, utilizationById) {
    let state = status(s, o.threshold);
    if (utilizationById && Object.hasOwn(utilizationById, s.id)) {
      const u = utilizationById[s.id];
      if (!Number.isFinite(u) || u < 0 || u > 1) throw new Error('예측 이용률은 0~1이어야 합니다.');
      state = 1 - u >= o.threshold ? '여유' : u < 1 ? '보통' : '혼잡';
    }
    const mult = !policy ? 1 : state === '여유' ? 1 - o.discount : state === '혼잡' && policy === 1 ? 1 + o.surcharge : 1;
    return Math.round(s.base * mult * 10) / 10;
  }
  function probability(costs, tau) {
    const finite = costs.filter(Number.isFinite);
    if (!finite.length) throw new Error('처리 가능한 충전기가 없습니다.');
    const min = Math.min(...finite), weights = costs.map(c => Number.isFinite(c) ? Math.exp(-(c - min) / tau) : 0);
    const sum = weights.reduce((a, b) => a + b, 0);
    return weights.map(w => w / sum);
  }
  function validate(stations, o) {
    const ranges = { drivers: [1, 500, true], minutes: [1, 1440], energy: [.1, 200], discount: [0, 1], surcharge: [0, 1],
      alpha: [0, 100000], beta: [0, 100000], tau: [1, 100000], sensitivity: [0, 10], repeats: [1, 500, true],
      seed: [0, 4294967295, true], distance: [0, 200], threshold: [.1, .9] };
    for (const [key, [lo, hi, integer]] of Object.entries(ranges)) {
      if (!Number.isFinite(o[key]) || o[key] < lo || o[key] > hi || (integer && !Number.isInteger(o[key])))
        throw new Error(`${key}: ${lo}~${hi}${integer ? ' 정수' : ''} 값을 입력하세요.`);
    }
    if (!['assumed', 'power'].includes(o.durationMode)) throw new Error('충전시간 계산 방식을 확인하세요.');
    if (stations.length !== 2 || stations[0].id === stations[1].id || stations[0].speed !== stations[1].speed)
      throw new Error('서로 다른 같은 종류의 충전소 A/B를 선택하세요.');
    for (const s of stations) {
      if (!['fast', 'slow'].includes(s.speed)) throw new Error('급속 또는 완속만 비교할 수 있습니다.');
      for (const k of ['avail', 'charging', 'fault']) if (!Number.isInteger(s[k]) || s[k] < 0 || s[k] > 1000) throw new Error('충전기 대수를 확인하세요.');
      if (!(s.base > 0) || !Number.isFinite(s.base) || !(s.kw > 0) || !Number.isFinite(s.kw)) throw new Error('기본 단가와 출력을 확인하세요.');
    }
    if (!stations.some(s => s.avail + s.charging > 0)) throw new Error('두 충전소 모두 처리 가능한 충전기가 없습니다.');
  }
  function population(stations, o, random) {
    // Fixed random consumption, independent of policy and choices.
    const initial = stations.map(s => Array.from({ length: s.charging }, () => random()));
    const drivers = Array.from({ length: o.drivers }, (_, id) => ({ id, arrival: random() * o.minutes, duration: 20 + random() * 20, choice: random() }))
      .sort((a, b) => a.arrival - b.arrival || a.id - b.id);
    return { initial, drivers };
  }
  const duration = (s, o, assumed) => o.durationMode === 'power' ? o.energy / s.kw * 60 : assumed;
  function runPolicy(stations, o, people, policy, utilizationById) {
    const prices = stations.map(s => price(s, policy, o, utilizationById));
    const queues = stations.map((s, i) => [
      ...Array(s.avail).fill(0), ...people.initial[i].map(u => u * duration(s, o, 30))
    ]);
    const records = [], count = [0, 0];
    let wait = 0, estimated = 0, charge = 0, distance = 0, discount = 0, completion = 0;
    for (const d of people.drivers) {
      const next = queues.map(q => q.length ? Math.min(...q) : Infinity);
      const expected = next.map(t => Math.max(0, t - d.arrival));
      const costs = stations.map((s, i) => o.sensitivity * o.energy * prices[i] + o.alpha * (i ? o.distance : 0) + o.beta * expected[i]);
      // An unusable station must stay excluded even if beta=0 (0 * Infinity = NaN).
      const probs = probability(costs.map((c, i) => queues[i].length ? c : Infinity), o.tau);
      const i = d.choice < probs[0] ? 0 : 1, q = queues[i], slot = q.indexOf(next[i]);
      const start = Math.max(d.arrival, next[i]), actual = start - d.arrival;
      const end = start + duration(stations[i], o, d.duration);
      q[slot] = end; count[i]++; wait += actual; estimated += expected[i];
      charge += o.energy * prices[i]; distance += i ? o.distance : 0;
      discount += o.energy * Math.max(0, stations[i].base - prices[i]); completion = Math.max(completion, end);
      records.push({ driver: d.id, station: i, arrival: d.arrival, expectedWait: expected[i], actualWait: actual, start, end });
    }
    return { policy, prices, count, share: count.map(n => n / o.drivers), wait: wait / o.drivers,
      expectedWait: estimated / o.drivers, fee: charge / o.drivers, distance: distance / o.drivers,
      discount, revenue: charge, completion, records };
  }
  async function compare(stations, options = {}, onProgress = () => {}, utilizationById) {
    const o = { ...defaults, ...options }; validate(stations, o);
    const random = rng(o.seed), results = policies.map((label, policy) => ({ label, policy, count: [0, 0], share: [0, 0], wait: 0, expectedWait: 0, fee: 0, distance: 0, discount: 0, revenue: 0, completion: 0, samples: [] }));
    for (let r = 0; r < o.repeats; r++) {
      const people = population(stations, o, random);
      for (let p = 0; p < 3; p++) {
        const run = runPolicy(stations, o, people, p, utilizationById), out = results[p];
        for (const key of ['wait', 'expectedWait', 'fee', 'distance', 'discount', 'revenue', 'completion']) out[key] += run[key] / o.repeats;
        for (let i = 0; i < 2; i++) { out.count[i] += run.count[i] / o.repeats; out.share[i] += run.share[i] / o.repeats; }
        out.samples.push(run.wait); out.prices = run.prices;
        if (!r) out.records = run.records;
      }
      if (r % 10 === 0) { onProgress((r + 1) / o.repeats); await new Promise(resolve => setTimeout(resolve, 0)); }
    }
    for (const out of results) {
      out.waitChange = results[0].wait > 1e-9 ? (out.wait / results[0].wait - 1) * 100 : null;
      out.revenueChange = out.revenue - results[0].revenue;
      const variance = o.repeats > 1 ? out.samples.reduce((s, x) => s + (x - out.wait) ** 2, 0) / (o.repeats - 1) : 0;
      out.waitSE = o.repeats > 1 ? Math.sqrt(variance / o.repeats) : null;
      delete out.samples;
    }
    onProgress(1); return { options: o, stations: stations.map(s => ({ ...s })), results };
  }
  return { defaults, policies, demo, rng, status, price, probability, validate, population, runPolicy, compare, forecast };
});
