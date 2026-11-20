const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  const storage = new Map();
  const timers = new Map();
  let sequence = 0;
  const context = { exports: {}, window: {
    localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) },
    setTimeout: fn => { timers.set(++sequence, fn); return sequence; },
    clearTimeout: id => timers.delete(id),
  }};
  const source = fs.readFileSync(path.join(__dirname, '../src/chartOverlayStore.ts'), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  }}).outputText, context);
  const overlays = new Map();
  let id = 0;
  let scope = 'HPG:1D';
  const chart = {
    createOverlay: item => { const key = String(++id); overlays.set(key, {currentStep: -1, ...item}); return key; },
    getOverlayById: id => overlays.get(id),
    overrideOverlay: override => { for (const overlay of overlays.values()) if (override.visible !== undefined) overlay.visible = override.visible; },
    removeOverlay: () => overlays.clear(),
    applyNewData: () => {}, resize: () => {},
  };
  const api = context.exports;
  api.attachOverlayPersistence(chart, () => scope);
  api.syncOverlayScope();
  return { api, chart, overlays, storage, switchTo(next) {
    scope = next; chart.applyNewData([]); api.syncOverlayScope();
  }, drain() { for (const fn of [...timers.values()]) fn(); timers.clear(); } };
}

test('Elliott six anchors and future rectangle survive symbol/timeframe roundtrips', () => {
  const h = setup();
  const wave = {name:'fiveWaves', totalStep:7, points:[0,1,2,3,4,5].map(i => i < 4
    ? {timestamp:1000+i, value:20+i} : {dataIndex:100+i, value:20+i})};
  const rect = {name:'rect', totalStep:3, points:[{dataIndex:110,value:25},{dataIndex:120,value:30}]};
  h.chart.createOverlay(wave); h.chart.createOverlay(rect);
  // Switch before the debounced save fires.
  h.switchTo('HPG:1W'); h.drain();
  assert.equal(h.overlays.size, 0);
  h.switchTo('HPG:1D');
  assert.equal(h.overlays.size, 2);
  assert.deepEqual(JSON.parse(JSON.stringify([...h.overlays.values()].map(o=>o.points))), [wave.points,rect.points]);
  h.switchTo('SSI:1D'); h.drain(); h.switchTo('HPG:1D');
  assert.equal([...h.overlays.values()][0].points.length, 6);
  assert.equal([...h.overlays.values()][1].points.length, 2);
  h.api.flushOverlaySave();
  const saved = h.storage.get('bookfin:overlays:v1:HPG:1D');
  h.api.attachOverlayPersistence(h.chart, () => 'HPG:1D');
  h.chart.removeOverlay(); h.api.syncOverlayScope();
  assert.equal(h.overlays.size, 2);
  assert.equal(JSON.parse(saved)[0].points.length, 6);
});

test('legacy timestamp-only drawings still restore and explicit clearing stays cleared', () => {
  const h = setup();
  h.storage.set('bookfin:overlays:v1:SSI:1D', JSON.stringify([{name:'rect', points:[{timestamp:1,value:2},{timestamp:2,value:3}]}]));
  h.switchTo('SSI:1D'); assert.equal(h.overlays.size,1);
  h.api.clearOverlays(); h.drain(); h.switchTo('HPG:1D'); h.switchTo('SSI:1D');
  assert.equal(h.overlays.size,0);
});

test('global drawing visibility survives reload after a realtime update', () => {
  const h = setup();
  h.chart.createOverlay({name:'rect', totalStep:3, points:[{timestamp:1,value:20},{timestamp:2,value:21}]});
  h.chart.overrideOverlay({visible:false});
  h.chart.updateData?.({timestamp:2,open:20,high:21,low:19,close:20,volume:1});
  assert.equal(JSON.parse(h.storage.get('bookfin:overlays:v1:HPG:1D'))[0].visible, false);
  h.api.attachOverlayPersistence(h.chart, () => 'HPG:1D');
  h.chart.removeOverlay();
  h.api.syncOverlayScope();
  assert.equal([...h.overlays.values()][0].visible, false);
});
