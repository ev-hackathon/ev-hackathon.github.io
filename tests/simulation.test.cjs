const { test } = require('node:test');
const assert = require('node:assert/strict');
const sim = require('../web/simulation.js');
const fs = require('node:fs');
const vm = require('node:vm');

test('all generated inline JavaScript parses and status matches existing monitor classifier', () => {
  const html = fs.readFileSync(require.resolve('../docs/index.html'), 'utf8');
  for (const match of html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
    if (!match[1].includes('application/json')) new vm.Script(match[2]);
  }
  const template = fs.readFileSync(require.resolve('../web/index.template.html'), 'utf8');
  const classifier = template.match(/function classifyStatus\([^]*?\n}/)[0];
  const context = {}; vm.runInNewContext(classifier + '; this.classify = classifyStatus;', context);
  for (let avail = 0; avail < 6; avail++) for (let charging = 0; charging < 6; charging++) for (const fault of [0, 3]) for (const threshold of [.1, .5, .9]) {
    assert.equal(sim.status({ avail, charging, fault }, threshold), context.classify(avail, charging, fault, threshold));
  }
});

test('zero discount and surcharge give identical policies with common random numbers', async () => {
  const { results } = await sim.compare(sim.demo(), { discount: 0, surcharge: 0, repeats: 20 });
  for (const r of results.slice(1)) for (const key of ['count', 'wait', 'fee', 'discount', 'revenue', 'records']) assert.deepEqual(r[key], results[0][key]);
});
test('same seed reproduces all output; another seed changes arrivals', async () => {
  const a = await sim.compare(sim.demo(), { repeats: 5 });
  assert.deepEqual(a, await sim.compare(sim.demo(), { repeats: 5 }));
  assert.notDeepEqual(a.results[0].records, (await sim.compare(sim.demo(), { repeats: 5, seed: 43 })).results[0].records);
});
test('unusable chargers excluded, including zero waiting cost', async () => {
  const stations = sim.demo(); Object.assign(stations[0], { avail: 0, charging: 0, fault: 4, base: 1 });
  const { results } = await sim.compare(stations, { repeats: 3, beta: 0 });
  for (const r of results) assert.deepEqual(r.count, [0, 24]);
  stations[1].avail = stations[1].charging = 0;
  await assert.rejects(sim.compare(stations), /처리 가능한/);
});
test('fully occupied initial station waits until initial release and includes completions after horizon', () => {
  const stations = sim.demo(), o = { ...sim.defaults, drivers: 2, minutes: 1 };
  Object.assign(stations[1], { avail: 0, charging: 0, fault: 4 });
  const r = sim.runPolicy(stations, o, { initial: [[.5, .5, .5, .5], []], drivers: [
    { id: 0, arrival: 0, duration: 40, choice: .1 }, { id: 1, arrival: .5, duration: 40, choice: .2 }
  ] }, 0);
  assert.equal(r.records[0].start, 15); assert.equal(r.records[0].actualWait, 15);
  assert.equal(r.records[1].start, 15); assert.equal(r.completion, 55); assert.deepEqual(r.count, [2, 0]);
});
test('single charger serves arrivals FIFO without overlap, all waiting times nonnegative', () => {
  const stations = sim.demo(); Object.assign(stations[0], { avail: 0, charging: 1 }); Object.assign(stations[1], { avail: 0, charging: 0, fault: 4 });
  const o = { ...sim.defaults, drivers: 30, minutes: 5 }, people = sim.population(stations, o, sim.rng(42));
  const r = sim.runPolicy(stations, o, people, 1);
  for (let i = 0; i < r.records.length; i++) {
    const d = r.records[i]; assert.ok(d.actualWait >= 0); assert.ok(d.start >= d.arrival);
    assert.equal(d.expectedWait, d.actualWait);
    if (i) assert.ok(d.start >= r.records[i - 1].end);
  }
});
test('discount only never surcharges busy station; distinct base prices and rounding preserved', () => {
  const [a, b] = sim.demo(), o = { ...sim.defaults };
  b.base = 400;
  assert.equal(sim.price(a, 0, o), 347.2); assert.equal(sim.price(b, 0, o), 400);
  assert.equal(sim.price(a, 1, o), 399.3); assert.equal(sim.price(a, 2, o), 347.2);
  assert.equal(sim.price(b, 1, o), 320); assert.equal(sim.price(b, 2, o), 320);
  assert.equal(sim.status({ avail: 1, charging: 1, fault: 8 }), '여유');
  assert.equal(sim.status({ avail: 1, charging: 3, fault: 0 }), '보통');
});
test('softmax is stable at extreme costs, excludes infinite costs', () => {
  assert.deepEqual(sim.probability([1e12, 1e12], 1), [.5, .5]);
  assert.deepEqual(sim.probability([Infinity, 1e12], 1), [0, 1]);
  assert.deepEqual(sim.probability([0, 1e12], 1), [1, 0]);
});
test('validation rejects mixed speeds, identical stations, invalid counts and inputs', async () => {
  for (const options of [{ tau: 0 }, { seed: -1 }, { repeats: 1.5 }, { discount: NaN }, { energy: 0 }]) await assert.rejects(sim.compare(sim.demo(), options));
  const stations = sim.demo(); stations[1].speed = 'slow'; await assert.rejects(sim.compare(stations));
  await assert.rejects(sim.compare([stations[0], stations[0]]));
});
test('power duration uses per-station output and initial remaining time', () => {
  const stations = sim.demo(), o = { ...sim.defaults, durationMode: 'power' };
  stations[0].kw = 30; Object.assign(stations[1], { avail: 0, charging: 0 });
  const r = sim.runPolicy(stations, o, { initial: [[.5, .5, .5, .5], []], drivers: [{ id: 0, arrival: 0, duration: 20, choice: .1 }] }, 0);
  assert.equal(r.records[0].start, 30); assert.equal(r.records[0].end, 90);
});
test('discount benefit and revenue differences are accounting identities', async () => {
  const { results, stations, options: o } = await sim.compare(sim.demo(), { repeats: 7 });
  for (const r of results) {
    assert.ok(Math.abs(r.count[0] + r.count[1] - o.drivers) < 1e-9);
    assert.ok(Math.abs(r.revenue - r.fee * o.drivers) < 1e-7);
    assert.ok(Math.abs(r.discount - r.count.reduce((sum, n, i) => sum + n * o.energy * Math.max(0, stations[i].base - r.prices[i]), 0)) < 1e-7);
    assert.equal(r.revenueChange, r.revenue - results[0].revenue);
  }
});
test('future utilization adapter only uses explicit input, absent values use initial state', () => {
  const [a] = sim.demo();
  assert.equal(sim.price(a, 1, sim.defaults), 399.3);
  assert.equal(sim.price(a, 1, sim.defaults, { 'DEMO-A': .2 }), 277.8);
  assert.throws(() => sim.price(a, 1, sim.defaults, { 'DEMO-A': 2 }));
});

test('forecast JSON validates IDs, speed, timestamps, duplicates, bounds and coverage', () => {
  const now = Date.parse('2026-11-02T12:00:00+09:00');
  const payload = { schema_version: 1, model_name: 'test fixture', generated_at: '2026-11-02T11:55:00+09:00', target_time: '2026-11-02T13:00:00+09:00',
    predictions: [{ station_id: 'DEMO-A', speed: 'fast', utilization: .2 }, { station_id: 'DEMO-B', speed: 'fast', utilization: 1 }] };
  const p = sim.forecast(payload, sim.demo(), now);
  assert.equal(p.utilizationById['DEMO-A'], .2);
  for (const change of [{ target_time: '2026-11-02T11:00:00+09:00' }, { generated_at: '2026-11-02T12:30:00+09:00' }, { model_name: '' },
    { predictions: payload.predictions.slice(0, 1) }, { predictions: [...payload.predictions, payload.predictions[0]] },
    { predictions: payload.predictions.map(r => ({ ...r, speed: 'slow' })) },
    { predictions: payload.predictions.map(r => ({ ...r, utilization: 1.1 })) }]) assert.throws(() => sim.forecast({ ...payload, ...change }, sim.demo(), now));
});

test('forecast changes policy prices and choices while preserving observed initial occupancy', async () => {
  const stations = sim.demo(), options = { repeats: 3, seed: 42 };
  const current = await sim.compare(stations, options);
  const predicted = await sim.compare(stations, options, () => {}, { 'DEMO-A': .2, 'DEMO-B': 1 });
  assert.deepEqual(current.results[0].records, predicted.results[0].records);
  assert.deepEqual(predicted.results[1].prices, [277.8, 399.3]);
  assert.deepEqual(predicted.results[2].prices, [277.8, 347.2]);
  assert.deepEqual(predicted.stations, stations);
  assert.notDeepEqual(current.results[1].records, predicted.results[1].records);
});
