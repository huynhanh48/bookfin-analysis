import { attachWorkspacePersistence, readWorkspaceSettings } from "./workspaceSettingsStore";
import {
  KLineChartPro,
  type ChartProOptions,
  type Period,
  type SymbolInfo,
} from "@klinecharts/pro";
import { dispose, type Chart } from "klinecharts";
import { BookfinDatafeed } from "./bookfinDatafeed";
import {
  attachOverlayPersistence,
  clearOverlays,
  flushOverlaySave,
  syncOverlayScope,
} from "./chartOverlayStore";
import { flushChartSettings, hydrateChartSettings } from "./chartSettingsStore";
import { installIndicatorMenu } from "./indicatorMenu";
import { applyIndicatorOverrides, attachIndicatorPersistence, flushIndicatorLayout } from "./indicatorLayoutStore";
import { mainIndicatorNames, setChartBridge, subIndicators, type ChartBridge } from "./indicatorState";
import { registerDivergence } from "./indicators/divergence";
import { registerDmi } from "./indicators/dmi";
import { registerIchimoku } from "./indicators/ichimoku";
import { registerMacd } from "./indicators/macd";
import { registerRsi } from "./indicators/rsi";
import { installLiveSymbolSearch } from "./symbolSearch";
import { registerWaveOverlays } from "./overlays/waveOverlays";
import { registerFibonacci } from "./overlays/fibonacci";
import { registerOverlay } from "klinecharts";
import { measure } from "./overlays/measure";
import { bindDrawingTools, installDrawingTools } from "./drawingTools";
import { installHeaderNav } from "./headerNav";

const DEFAULT_SYMBOL: SymbolInfo = {
  ticker: "SSI",
  name: "SSI Securities Corporation",
  shortName: "SSI",
  exchange: "HOSE",
  market: "stocks",
  priceCurrency: "nghìn đồng",
  pricePrecision: 2,
  volumePrecision: 0,
};

const DEFAULT_PERIOD: Period = { multiplier: 1, timespan: "day", text: "1D" };

let stopWorkspacePersistence: (() => void) | null = null;
let datafeed: BookfinDatafeed | null = null;
let chart: KLineChartPro | null = null;
let container: HTMLElement | null = null;
let chartGeneration = 0;

function setStatus(message: string, level: "ok" | "error"): void {
  const status = document.getElementById("status");
  if (!status) return;
  status.textContent = message;
  status.dataset.level = level;
}

/** Khoá phạm vi lưu nét vẽ: mỗi mã + khung thời gian có bộ overlay riêng. */
function overlayScope(): string {
  const symbol = chart?.getSymbol();
  const period = chart?.getPeriod();
  return `${symbol?.ticker ?? "?"}:${period?.text ?? "?"}`;
}

/** Chart instance thật do patch của Pro cung cấp; có thể không tồn tại. */
function chartInstance(instance: KLineChartPro): Chart | null {
  const api = instance as unknown as { _chartApi?: { getChart?: () => Chart | null } };
  return api._chartApi?.getChart?.() ?? null;
}

/**
 * Gắn persistence sau khi Pro đã gán chart API.
 *
 * `_chartApi` do Solid ref gán trong/after render, nên có thể chưa có ngay sau
 * `new KLineChartPro()`. Retry vài frame thay vì bỏ qua vĩnh viễn — nếu bỏ qua,
 * trạng thái ẩn/hiện indicator không bao giờ được lưu hoặc khôi phục.
 */
function bindChartFeatures(
  instance: KLineChartPro,
  host: HTMLElement,
  generation: number,
  attempt = 0,
): void {
  if (generation !== chartGeneration || chart !== instance) return;
  const klineChart = chartInstance(instance);
  if (!klineChart) {
    if (attempt < 100) {
      window.setTimeout(() => bindChartFeatures(instance, host, generation, attempt + 1), 50);
    } else {
      console.warn("[bookfin] Không lấy được chart instance; bỏ qua persistence.");
    }
    return;
  }

  attachOverlayPersistence(klineChart, overlayScope, host);
  attachIndicatorPersistence(klineChart);
  // Keep the latest candle next to the price axis instead of a large blank
  // right gutter. Pro otherwise leaves the default 80px offset, which makes
  // the series look cut off when zoomed out.
  try {
    klineChart.setOffsetRightDistance(8);
  } catch {
    // Older chart builds may not expose the optional viewport helper.
  }
  bindDrawingTools((options) => klineChart.createOverlay(options));
  if (import.meta.env.DEV) {
    // Chỉ tồn tại ở dev, dùng để kiểm tra/ tự động hoá thao tác chart.
    (window as unknown as Record<string, unknown>).__bookfinChart = klineChart;
  }

  // Áp lại ẩn/hiện sau khi chart đã có indicator; callback datafeed có thể đã
  // chạy trước lúc chart API sẵn sàng nên cần gọi lại ở đây.
  window.setTimeout(() => {
    if (chart !== instance) return;
    applyIndicatorOverrides();
    // The history callback can run before Pro exposes its underlying Chart.
    // Restore drawings after both the API and the first data list exist.
    syncOverlayScope();
    window.setTimeout(() => {
      if (chart === instance) syncOverlayScope();
    }, 180);
  }, 0);
}

function mount(symbol: SymbolInfo, period: Period): void {
  if (!container) return;
  chartGeneration += 1;
  const generation = chartGeneration;
  if (chart) {
    datafeed?.close();
    stopWorkspacePersistence?.();
    flushIndicatorLayout();
    flushChartSettings();
    flushOverlaySave();
    dispose(container);
    chart = null;
  }
  container.innerHTML = "";

  const savedWorkspace = readWorkspaceSettings();
  datafeed = new BookfinDatafeed(setStatus, () => {
    // Chờ Pro applyNewData/applyMoreData xong rồi mới khôi phục nét vẽ.
    window.setTimeout(() => {
      syncOverlayScope();
      applyIndicatorOverrides();
    }, 120);
  });
  const options: ChartProOptions = {
    container,
    locale: savedWorkspace.locale ?? "en-US",
    timezone: savedWorkspace.timezone ?? "Asia/Ho_Chi_Minh",
    theme: savedWorkspace.theme ?? "light",
    drawingBarVisible: true,
    // Tắt logo mặc định của Pro ở giữa pane giá.
    watermark: "",
    symbol: { ...symbol, priceCurrency: "nghìn đồng" },
    period,
    periods: [
      { multiplier: 1, timespan: "day", text: "1D" },
      { multiplier: 1, timespan: "week", text: "1W" },
    ],
    mainIndicators: mainIndicatorNames(),
    subIndicators: [...subIndicators],
    datafeed,
  };

  const instance = new KLineChartPro(options);
  chart = instance;
  if (savedWorkspace.styles) instance.setStyles(savedWorkspace.styles);
  stopWorkspacePersistence = attachWorkspacePersistence(instance);
  bindChartFeatures(instance, container, generation);
}

/**
 * Pro không expose chart instance nên chỉ báo custom phải áp dụng theo hai cách:
 * vẽ lại bằng `setPeriod` (rẻ, dùng cho tick đường Ichimoku) và dựng lại chart
 * (dùng cho thêm/bớt indicator trên pane giá).
 */
const bridge: ChartBridge = {
  repaint: () => {
    if (!chart) return;
    // Nạp lại đúng khung hiện tại: Pro tính lại indicator và vẽ lại toàn bộ pane.
    chart.setPeriod({ ...chart.getPeriod() });
  },
  toggleMain: () => {
    mount(chart?.getSymbol() ?? DEFAULT_SYMBOL, chart?.getPeriod() ?? DEFAULT_PERIOD);
  },
};

export default function setupApp(root: HTMLDivElement): void {
  root.innerHTML = `<div id="container"></div>`;

  // Khôi phục lựa chọn đã lưu trước khi dựng chart.
  hydrateChartSettings();

  registerIchimoku();
  registerDivergence();
  registerMacd();
  registerRsi();
  registerDmi();
  registerWaveOverlays();
  registerFibonacci();
  registerOverlay(measure);

  container = document.getElementById("container") as HTMLElement;
  setChartBridge(bridge);
  installIndicatorMenu();
  installLiveSymbolSearch();
  installDrawingTools();
  installHeaderNav(
    () => chart,
    () => {
      clearOverlays();
      setStatus("Đã xoá nét vẽ của mã/khung hiện tại.", "ok");
    },
  );

  window.addEventListener("bookfin:fib-levels-changed", () => {
    // Vẽ lại để overlay Fibonacci cập nhật mức mới (state đọc lúc render).
    chart?.setPeriod({ ...chart.getPeriod() });
  });

  window.addEventListener("beforeunload", () => {
    flushIndicatorLayout();
    flushChartSettings();
    flushOverlaySave();
  });

  window.addEventListener("pagehide", () => {
    flushIndicatorLayout();
    flushChartSettings();
    flushOverlaySave();
  });
  const saved = readWorkspaceSettings();
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("symbol")?.trim().toUpperCase();
  const symbol = requested
    ? { ...DEFAULT_SYMBOL, ticker: requested, shortName: requested, name: requested }
    : (saved.symbol ?? DEFAULT_SYMBOL);
  const timeframe = params.get("timeframe")?.trim().toUpperCase();
  const period: Period =
    timeframe === "1W"
      ? { multiplier: 1, timespan: "week", text: "1W" }
      : (saved.period ?? DEFAULT_PERIOD);
  mount(symbol, period);
}
