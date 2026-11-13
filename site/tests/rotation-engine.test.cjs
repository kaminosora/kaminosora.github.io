'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const { createLibrary } = require('../rotation-engine.js');
const neutral = { noveltyBoost: 0, recentPenalty: 0 };
const ip = (id, extra = {}) => ({ id, enabled: true, rotation: { enabled: true, weight: 1 }, ...extra });
const character = (id, ipId, extra = {}) => ({ id, ipId, enabled: true, weight: 1, ...extra });
const art = (id, ipId, characterIds = [], extra = {}) => ({ id, ipId, characterIds, enabled: true, uses: ['hero', 'gallery'], weight: 1, dimensions: [2000, 2000], ...extra });
const fixture = () => ({
  ips: [ip('a'), ip('b')],
  characters: [character('a1', 'a'), character('a2', 'a'), character('b1', 'b')],
  assets: [art('a1-1', 'a', ['a1']), art('a1-2', 'a', ['a1']), art('a2-1', 'a', ['a2']), art('b1-1', 'b', ['b1'])]
});
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);
const probabilities = (engine, query) => Object.fromEntries(engine.distribution(query).map(item => [item.id, item.probability]));

test('normalizes the IP → character → image hierarchy without favoring image-rich characters', () => {
  const engine = createLibrary(fixture(), neutral);
  const p = probabilities(engine);
  close(p['a1-1'], 0.125); close(p['a1-2'], 0.125);
  close(p['a2-1'], 0.25); close(p['b1-1'], 0.5);
  close(Object.values(p).reduce((a, b) => a + b), 1);
  const conditional = probabilities(engine, { ipId: 'a' });
  close(conditional['a1-1'] + conditional['a1-2'], 0.5);
  close(conditional['a2-1'], 0.5);
});

test('applies explicit weights at their own hierarchical level', () => {
  const data = fixture();
  data.ips[0].rotation.weight = 3;
  data.characters[0].weight = 3;
  data.assets[0].weight = 2;
  const p = probabilities(createLibrary(data, neutral));
  close(p['a1-1'], 0.375); close(p['a1-2'], 0.1875);
  close(p['a2-1'], 0.1875); close(p['b1-1'], 0.25);
});

test('excludes disabled, zero-weight, invalid, and wrong-use entries', () => {
  const data = fixture();
  data.ips.push(ip('disabled', { enabled: false }), ip('rotation-off', { rotation: { enabled: false } }), ip('zero', { rotation: { weight: 0 } }));
  data.characters.push(character('off', 'a', { enabled: false }), character('zero', 'a', { weight: 0 }));
  data.assets.push(
    art('disabled-ip', 'disabled'), art('disabled-rotation', 'rotation-off'), art('zero-ip', 'zero'),
    art('disabled-art', 'a', [], { enabled: false }), art('zero-art', 'a', [], { weight: 0 }),
    art('disabled-char', 'a', ['off']), art('zero-char', 'a', ['zero']),
    art('unknown-character', 'a', ['missing']), art('wrong-ip-character', 'a', ['b1']),
    art('missing-ip', 'missing'), art('only-gallery', 'a', [], { uses: ['gallery'] }),
    art('missing-use', 'a', [], { uses: [] }), art('invalid-weight', 'a', [], { weight: NaN })
  );
  const engine = createLibrary(data);
  assert.deepEqual(engine.distribution().map(item => item.id), ['a1-1', 'a1-2', 'a2-1', 'b1-1']);
  assert.ok(engine.distribution({ use: 'gallery' }).some(item => item.id === 'only-gallery'));
  assert.deepEqual(engine.distribution({ use: 'avatar' }), []);
});

test('new images, characters, and IPs join without hardcoded registries or engine reconstruction', () => {
  const data = fixture();
  const engine = createLibrary(data, neutral);
  data.assets.push(art('a2-2', 'a', ['a2']));
  let p = probabilities(engine, { ipId: 'a' });
  close(p['a2-1'], 0.25); close(p['a2-2'], 0.25);
  data.characters.push(character('a3', 'a'));
  data.assets.push(art('a3-1', 'a', ['a3']));
  p = probabilities(engine, { ipId: 'a' });
  close(p['a3-1'], 1 / 3);
  data.ips.push(ip('c'));
  data.assets.push(art('c-scene', 'c'));
  p = probabilities(engine);
  close(p['c-scene'], 1 / 3);
});

test('gallery and other uses have independent IP weights and do not inherit the hero switch', () => {
  const data = {
    ips: [ip('personal', { rotation: { enabled:false,weight:0 }, weight:3 }), ip('other', { rotation: { enabled:true,weight:100 }, weight:1 })],
    assets: [art('personal-work','personal',[],{uses:['gallery','card']}), art('other-work','other',[],{uses:['hero','gallery','card']})]
  };
  const engine = createLibrary(data, neutral);
  const hero = probabilities(engine,{use:'hero'});
  assert.deepEqual(hero,{'other-work':1});
  const gallery = probabilities(engine,{use:'gallery'});
  close(gallery['personal-work'],0.75); close(gallery['other-work'],0.25);
  close(probabilities(engine,{use:'card'})['personal-work'],0.75);
  data.ips[0].enabled = false;
  assert.deepEqual(probabilities(engine,{use:'gallery'}),{'other-work':1});
  data.ips[0].enabled = true; data.ips[0].weight = 0;
  assert.deepEqual(probabilities(engine,{use:'gallery'}),{'other-work':1});
});

test('excludes immediate repeats while an alternative exists and allows a singleton', () => {
  const engine = createLibrary(fixture());
  let last;
  for (let index = 0; index < 100; index += 1) {
    const asset = engine.next({}, () => 0);
    assert.notEqual(asset.id, last);
    const p = probabilities(engine);
    assert.equal(p[asset.id], 0);
    close(Object.values(p).reduce((a, b) => a + b), 1);
    last = asset.id;
  }
  const single = createLibrary({ ips: [ip('a')], assets: [art('one', 'a')] });
  assert.equal(single.next().id, 'one'); assert.equal(single.next().id, 'one');
});

test('honors conditional IP queries even when that IP only has the last shown image', () => {
  const engine = createLibrary(fixture());
  engine.record('b1-1');
  assert.equal(engine.next({ ipId: 'b' }).id, 'b1-1');
  assert.equal(engine.distribution({ ipId: 'a' }).length, 3);
});

test('groups multi-character images once with stable ordering and mean character weight', () => {
  const data = fixture();
  data.characters[0].weight = 3;
  data.assets = [art('group1', 'a', ['a2', 'a1']), art('group2', 'a', ['a1', 'a2', 'a1']), art('scene', 'a')];
  const distribution = createLibrary(data, neutral).distribution();
  assert.equal(distribution[0].characterGroup, distribution[1].characterGroup);
  distribution.forEach(item => close(item.probability, 1 / 3));
});

test('low exposure and recent history affect real next probabilities without permanent starvation', () => {
  const data = fixture();
  const engine = createLibrary(data, { recentWindow: 3 });
  for (let index = 0; index < 6; index += 1) engine.record('a1-1');
  engine.record('b1-1');
  const p = probabilities(engine, { ipId: 'a' });
  assert.ok(p['a1-2'] > p['a1-1']);
  assert.ok(p['a2-1'] > p['a1-1'] + p['a1-2']);
  assert.ok(p['a1-1'] > 0);
  const history = engine.getHistory();
  assert.equal(history.total, 7); assert.equal(history.recent.length, 3);
  history.counts['a1-1'] = 999; history.recent.length = 0;
  assert.equal(engine.getHistory().counts['a1-1'], 6);
  assert.equal(engine.getHistory().recent.length, 3);
});

test('quality and device framing only change image odds within their character group', () => {
  const data = fixture();
  data.assets[0].dimensions = [3000, 1000];
  data.assets[1].dimensions = [1000, 2000];
  data.assets[0].quality = 0.7;
  const engine = createLibrary(data, neutral);
  const mobile = probabilities(engine, { ipId: 'a', viewport: 'mobile' });
  const desktop = probabilities(engine, { ipId: 'a', viewport: 'desktop' });
  assert.ok(mobile['a1-2'] > desktop['a1-2']);
  close(mobile['a2-1'], 0.5); close(desktop['a2-1'], 0.5);
});

test('empty libraries and unrecognized manual records are safe', () => {
  for (const data of [undefined, {}, { ips: [], assets: [] }]) {
    const engine = createLibrary(data);
    assert.equal(engine.next(), null); assert.deepEqual(engine.distribution(), []);
    assert.equal(engine.record('missing'), false); assert.equal(engine.getHistory().total, 0);
  }
});

test('fixed random inputs at and outside both boundaries choose a valid positive-probability item', () => {
  for (const raw of [0, -1, -Infinity, NaN, 'invalid']) {
    assert.equal(createLibrary(fixture(), neutral).next({}, () => raw).id, 'a1-1');
  }
  for (const raw of [1, 2, Infinity]) {
    assert.equal(createLibrary(fixture(), neutral).next({}, () => raw).id, 'b1-1');
  }
  assert.equal(createLibrary(fixture(), neutral).next({}, () => 0.125).id, 'a1-2');
});

test('does not mutate any input, including character order and settings', () => {
  const data = fixture();
  data.assets[0].characterIds = ['a2', 'a1'];
  const before = JSON.stringify(data);
  function freeze(value) {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
  }
  const engine = createLibrary(freeze(data), freeze({ recentWindow: 2 }));
  engine.distribution(); engine.next(); engine.record('a1-2'); engine.getHistory();
  assert.equal(JSON.stringify(data), before);
});

test('large finite weights stay normalized and duplicate IDs cannot double their chance', () => {
  const data = fixture();
  data.ips.forEach(item => { item.rotation.weight = Number.MAX_VALUE; });
  data.assets.push({ ...data.assets[0] });
  const distribution = createLibrary(data).distribution();
  assert.equal(distribution.length, 4);
  close(distribution.reduce((sum, item) => sum + item.probability, 0), 1);
  assert.ok(distribution.every(item => Number.isFinite(item.probability)));
});

test('browser UMD exposes the same factory without requiring CommonJS', () => {
  const sandbox = {};
  vm.runInNewContext(fs.readFileSync(require.resolve('../rotation-engine.js'), 'utf8'), sandbox);
  assert.equal(typeof sandbox.SoraRotation.createLibrary, 'function');
  assert.equal(sandbox.SoraRotation.createLibrary({}).next(), null);
});
