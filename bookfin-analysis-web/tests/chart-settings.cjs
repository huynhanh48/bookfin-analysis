const { test } = require("node:test");
const assert = require("node:assert/strict");
const ts = require("typescript");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("disabled indicator groups and Fibonacci levels survive hydration", () => {
  const values = new Map();
  const state = {
    ichimokuSettings: { tenkan: true, kijun: true, senkouA: true, senkouB: true, chikou: true, cloud: true },
    mainIndicators: ["MA"],
    subIndicators: ["VOL"],
    showHiddenDivergence: true,
  };
  const fibonacci = { levels: [], fibLevels: () => fibonacci.levels, setFibLevels: value => { fibonacci.levels = value; } };
  const source = fs.readFileSync(path.join(__dirname, "../src/chartSettingsStore.ts"), "utf8");
  const context = {
    exports: {},
    require: request => request.includes("fibonacci")
      ? fibonacci
      : {
          ...state,
          setShowHiddenDivergence: value => { state.showHiddenDivergence = value; },
          replaceMainIndicators: value => { state.mainIndicators.splice(0, state.mainIndicators.length, ...value); },
          replaceSubIndicators: value => { state.subIndicators.splice(0, state.subIndicators.length, ...value); },
        },
    window: {
      localStorage: {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
      },
      setTimeout: fn => { fn(); return 1; },
      clearTimeout: () => {},
    },
  };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  values.set("bookfin:chart-settings:v1", JSON.stringify({
    mainIndicators: [],
    subIndicators: [],
    showHiddenDivergence: false,
    fibLevels: [],
    overrides: { MACD: { visible: false } },
  }));
  context.exports.hydrateChartSettings();
  assert.equal(state.mainIndicators.length, 0);
  assert.equal(state.subIndicators.length, 0);
  assert.equal(state.showHiddenDivergence, false);
  assert.equal(fibonacci.levels.length, 0);
  assert.equal(context.exports.indicatorOverrides().MACD.visible, false);
  context.exports.persistChartSettings();
  assert.equal(JSON.parse(values.get("bookfin:chart-settings:v1")).overrides.MACD.visible, false);
});

test("an async Pro hide command remains saved when its getter is stale at pagehide", () => {
  const overrides = {};
  const writes = [];
  const mainIndicators = ["MA"];
  const subIndicators = ["RSI"];
  const indicators = new Map([["MA", { visible: true, calcParams: [5, 10] }]]);
  const chart = {
    getIndicatorByPaneId: () => new Map([["candle_pane", indicators]]),
    createIndicator: () => "candle_pane",
    removeIndicator: () => {},
    // KLineCharts applies this asynchronously; its getter still says true.
    overrideIndicator: () => {},
  };
  const context = {
    exports: {},
    require: () => ({
      indicatorOverrides: () => overrides,
      replaceIndicatorOverrides: value => { Object.keys(overrides).forEach(key => delete overrides[key]); Object.assign(overrides, value); },
      persistChartSettings: () => writes.push(JSON.parse(JSON.stringify(overrides))),
      setMainIndicator: (name, enabled) => {
        const index = mainIndicators.indexOf(name);
        if (enabled && index < 0) mainIndicators.push(name);
        if (!enabled && index >= 0) mainIndicators.splice(index, 1);
      },
      subIndicators,
    }),
    window: { setTimeout: () => 1, clearTimeout: () => {} },
  };
  const source = fs.readFileSync(path.join(__dirname, "../src/indicatorLayoutStore.ts"), "utf8");
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  context.exports.attachIndicatorPersistence(chart);
  context.exports.applyIndicatorOverrides();
  chart.overrideIndicator({name: "MA", visible: false}, "candle_pane");
  context.exports.flushIndicatorLayout();
  assert.equal(overrides.MA.visible, false);
  assert.equal(writes.at(-1).MA.visible, false);
  chart.removeIndicator("candle_pane", "MA");
  chart.removeIndicator("rsi_pane", "RSI");
  assert.equal(mainIndicators.length, 0);
  assert.equal(subIndicators.length, 0);
  chart.createIndicator("MA", true, {id:"candle_pane"});
  chart.createIndicator("RSI");
  assert.deepEqual(mainIndicators, ["MA"]);
  assert.deepEqual(subIndicators, ["RSI"]);
});

test("close and hide choices hydrate after a full module reload", () => {
  const storage = new Map();
  const compile = filename => ts.transpileModule(
    fs.readFileSync(path.join(__dirname, `../src/${filename}.ts`), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  const start = () => {
    const state = {
      ichimokuSettings: { tenkan:true, kijun:true, senkouA:true, senkouB:true, chikou:true, cloud:true },
      mainIndicators: ["MA", "ICHIMOKU", "DIVERGENCE"],
      subIndicators: ["VOL", "MACD"],
      showHiddenDivergence: true,
    };
    const stateApi = {
      ...state,
      setShowHiddenDivergence: value => { state.showHiddenDivergence = value; },
      replaceMainIndicators: names => state.mainIndicators.splice(0, state.mainIndicators.length, ...names),
      replaceSubIndicators: names => state.subIndicators.splice(0, state.subIndicators.length, ...names),
      setMainIndicator: (name, enabled) => {
        const index = state.mainIndicators.indexOf(name);
        if (enabled && index < 0) state.mainIndicators.push(name);
        if (!enabled && index >= 0) state.mainIndicators.splice(index, 1);
      },
    };
    const settings = { exports: {}, window: { localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    } } };
    const fib = { fibLevels: () => [], setFibLevels: () => {} };
    settings.require = name => name.includes("fibonacci") ? fib : stateApi;
    vm.runInNewContext(compile("chartSettingsStore"), settings);
    const layout = { exports: {}, window: { setTimeout: () => 1, clearTimeout: () => {} } };
    layout.require = name => name.includes("chartSettingsStore") ? settings.exports : stateApi;
    vm.runInNewContext(compile("indicatorLayoutStore"), layout);
    return { state, settings: settings.exports, layout: layout.exports };
  };
  const first = start();
  const pane = new Map([
    ["MA", {visible:true, calcParams:[5,10]}],
    ["ICHIMOKU", {visible:true, calcParams:[]}],
  ]);
  const chart = {
    getIndicatorByPaneId: () => new Map([["candle_pane", pane]]),
    createIndicator: () => "candle_pane",
    removeIndicator: () => {},
    overrideIndicator: () => {}, // deliberately leaves the getter stale
  };
  first.layout.attachIndicatorPersistence(chart);
  first.layout.applyIndicatorOverrides();
  chart.overrideIndicator({name:"ICHIMOKU", visible:false}, "candle_pane");
  chart.removeIndicator("candle_pane", "MA");
  first.layout.flushIndicatorLayout();
  first.settings.flushChartSettings();
  const saved = JSON.parse(storage.get("bookfin:chart-settings:v1"));
  assert.equal(saved.overrides.ICHIMOKU.visible, false);
  assert.equal(saved.mainIndicators.includes("MA"), false);

  const afterReload = start();
  afterReload.settings.hydrateChartSettings();
  assert.equal(afterReload.state.mainIndicators.includes("MA"), false);
  assert.equal(afterReload.settings.indicatorOverrides().ICHIMOKU.visible, false);
});
