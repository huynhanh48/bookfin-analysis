import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import { formatCompact, formatPrice, signalTone } from "../format";
import type {
  CandleOut,
  IndicatorPoint,
  IndicatorSeries,
  IndicatorSeriesResponse,
  StructureSnapshot,
} from "../types";

const MARKET_TIME_ZONE = "Asia/Ho_Chi_Minh";

const axisDateFormatter = new Intl.DateTimeFormat("vi-VN", {
  timeZone: MARKET_TIME_ZONE,
  day: "2-digit",
  month: "2-digit",
});

const toChartTime = (iso: string): UTCTimestamp =>
  Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;

const formatParam = (value: number | string): string =>
  typeof value === "number" && Number.isInteger(value) ? String(value) : String(value);

const seriesLabel = (series: IndicatorSeries): string => {
  const params = Object.values(series.parameters).map(formatParam).join("/");
  const base = INDICATOR_LABELS[series.indicator] ?? series.indicator.toUpperCase();
  return params ? `${base} ${params}` : base;
};

const INDICATOR_LABELS: Record<string, string> = {
  sma: "SMA",
  ema: "EMA",
  bollinger: "BB",
  rsi: "RSI",
  macd: "MACD",
  volume_sma: "Vol SMA",
  adx: "ADX",
  atr: "ATR",
  obv: "OBV",
  mfi: "MFI",
  roc: "ROC",
};

const VALUE_LABELS: Record<string, string> = {
  macd: "MACD",
  signal_line: "Signal",
  histogram: "Hist",
  upper: "Upper",
  middle: "Mid",
  lower: "Lower",
  percent_b: "%B",
  plus_di: "+DI",
  minus_di: "-DI",
};

const SERIES_COLORS: Record<string, string> = {
  sma: "#f59e0b",
  ema: "#a78bfa",
  bollinger: "#94a3b8",
  rsi: "#38bdf8",
  macd: "#38bdf8",
  volume_sma: "#facc15",
};

const SMA_COLORS = ["#f59e0b", "#fb923c"];
const EMA_COLORS = ["#a78bfa", "#c084fc"];

/** Khoá giá trị hiển thị trong legend và vẽ trên biểu đồ. */
const hoverKeys = (series: IndicatorSeries): string[] => {
  if (series.indicator === "bollinger") return ["upper", "middle", "lower"];
  if (series.indicator === "macd") return ["macd", "signal_line", "histogram"];
  // Phân kỳ là cờ 0/1, hiển thị thành một dòng "Phân kỳ" thay vì bốn dòng số.
  if (series.indicator === "divergence") return [];
  const first = series.points[0];
  return first ? Object.keys(first.values).slice(0, 1) : [];
};

/** Các loại phân kỳ đang bật tại một nến, dùng cho legend. */
const activeDivergences = (values: Record<string, number | null>): string[] =>
  Object.entries(DIVERGENCE_LABELS)
    .filter(([key]) => values[key] === 1)
    .map(([, label]) => label);

const hoverLabel = (series: IndicatorSeries, key: string): string => {
  const base = seriesLabel(series);
  if (series.indicator === "macd" || series.indicator === "bollinger") {
    return `${base} · ${VALUE_LABELS[key] ?? key}`;
  }
  return base;
};

const colorFor = (series: IndicatorSeries, key: string): string => {
  if (series.indicator === "divergence") return "#a855f7";
  if (series.indicator === "bollinger") {
    return key === "middle" ? "#cbd5e1" : "#64748b";
  }
  if (series.indicator === "macd" && key === "signal_line") return "#f97316";
  if (series.indicator === "macd" && key === "histogram") return "#94a3b8";
  return SERIES_COLORS[series.indicator] ?? "#94a3b8";
};

interface MarkerStyle {
  position: "aboveBar" | "belowBar";
  color: string;
  text: string;
  shape: "arrowUp" | "arrowDown" | "circle";
}

const INDICATOR_MARKERS: Record<string, Record<string, MarkerStyle>> = {
  macd: {
    bullish_cross: { position: "belowBar", color: "#22c55e", text: "MACD↑", shape: "arrowUp" },
    bearish_cross: { position: "aboveBar", color: "#ef4444", text: "MACD↓", shape: "arrowDown" },
  },
  rsi: {
    oversold: { position: "belowBar", color: "#38bdf8", text: "RSI≤30", shape: "circle" },
    overbought: { position: "aboveBar", color: "#f97316", text: "RSI≥70", shape: "circle" },
  },
  divergence: {
    bullish_regular: { position: "belowBar", color: "#22c55e", text: "PK↑", shape: "arrowUp" },
    bearish_regular: { position: "aboveBar", color: "#ef4444", text: "PK↓", shape: "arrowDown" },
    bullish_hidden: { position: "belowBar", color: "#2dd4bf", text: "PK ẩn↑", shape: "circle" },
    bearish_hidden: { position: "aboveBar", color: "#f59e0b", text: "PK ẩn↓", shape: "circle" },
  },
};

/** Nhãn tiếng Việt cho các loại phân kỳ của chỉ báo divergence. */
const DIVERGENCE_LABELS: Record<string, string> = {
  bullish_regular: "Phân kỳ tăng",
  bearish_regular: "Phân kỳ giảm",
  bullish_hidden: "Phân kỳ ẩn tăng",
  bearish_hidden: "Phân kỳ ẩn giảm",
};

/** Nhãn tiếng Việt cho signal thô của backend, dùng trong legend. */
const SIGNAL_LABELS: Record<string, string> = {
  ...DIVERGENCE_LABELS,
  bullish_cross: "MACD cắt lên",
  bearish_cross: "MACD cắt xuống",
  above_signal: "MACD trên Signal",
  below_signal: "MACD dưới Signal",
  oversold: "Quá bán",
  overbought: "Quá mua",
  neutral: "Trung tính",
  trending: "Xu hướng mạnh",
  weak_trend: "Xu hướng yếu",
  above_cloud: "Trên mây",
  below_cloud: "Dưới mây",
  inside_cloud: "Trong mây",
  above_average: "KL trên TB",
  normal: "KL bình thường",
  outperform: "Mạnh hơn chỉ số",
  underperform: "Yếu hơn chỉ số",
  inline: "Ngang chỉ số",
};

const signalLabel = (signal: string): string => SIGNAL_LABELS[signal] ?? signal;

const STRUCTURE_MARKERS: Record<string, MarkerStyle> = {
  BOS_BULLISH: { position: "belowBar", color: "#22c55e", text: "BOS↑", shape: "arrowUp" },
  BOS_BEARISH: { position: "aboveBar", color: "#ef4444", text: "BOS↓", shape: "arrowDown" },
  CHOCH_BULLISH: { position: "belowBar", color: "#38bdf8", text: "CHoCH↑", shape: "arrowUp" },
  CHOCH_BEARISH: { position: "aboveBar", color: "#f97316", text: "CHoCH↓", shape: "arrowDown" },
  MSS_BULLISH: { position: "belowBar", color: "#22c55e", text: "MSS↑", shape: "arrowUp" },
  MSS_BEARISH: { position: "aboveBar", color: "#ef4444", text: "MSS↓", shape: "arrowDown" },
  FAILED_BREAKOUT_BULLISH: { position: "aboveBar", color: "#f59e0b", text: "FB↑", shape: "circle" },
  FAILED_BREAKOUT_BEARISH: { position: "belowBar", color: "#f59e0b", text: "FB↓", shape: "circle" },
};

export interface OverlayOptions {
  sma: boolean;
  ema: boolean;
  bollinger: boolean;
  divergence: boolean;
  divergenceMacd?: boolean;
  divergenceRsi?: boolean;
  divergenceBullish?: boolean;
  divergenceBearish?: boolean;
  divergenceLines?: boolean;
  divergenceLabels?: boolean;
}

interface ChartProps {
  data: IndicatorSeriesResponse;
  structure: StructureSnapshot | null;
  overlays: OverlayOptions;
}

interface HoverRow {
  label: string;
  value: string;
  color: string;
}

interface HoverInfo {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  rows: HoverRow[];
  signals: { label: string; tone: "up" | "down" | "flat" }[];
}

/** UI filtering does not change backend detection or event identities. */
const visibleDivergences = (data: IndicatorSeriesResponse, overlays: OverlayOptions) =>
  overlays.divergence ? data.series.flatMap(series => series.indicator === "divergence" ? series.events ?? [] : [])
    .filter(event => (event.direction === "bullish" ? overlays.divergenceBullish !== false : overlays.divergenceBearish !== false)
      && event.oscillators.some(osc => osc.indicator === "MACD" ? overlays.divergenceMacd !== false : overlays.divergenceRsi !== false))
    .sort((a, b) => Date.parse(a.confirmed_at) - Date.parse(b.confirmed_at) || a.id.localeCompare(b.id)).slice(-1) : [];

const divergenceLabel = (event: ReturnType<typeof visibleDivergences>[number], overlays: OverlayOptions) =>
  event.oscillators.filter(osc => osc.indicator === "MACD" ? overlays.divergenceMacd !== false : overlays.divergenceRsi !== false)
    .map(osc => osc.indicator).join(" + ") + (event.direction === "bullish" ? " Bull" : " Bear");

const buildMarkers = (
  data: IndicatorSeriesResponse,
  structure: StructureSnapshot | null,
  candleTimes: Set<number>,
  overlays: OverlayOptions,
): SeriesMarker<Time>[] => {
  const markers: SeriesMarker<Time>[] = [];

  for (const series of data.series) {
    if (series.indicator === "divergence") continue;
    const signalMap = INDICATOR_MARKERS[series.indicator];
    if (!signalMap) continue;
    let previous: string | null = null;
    for (const point of series.points) {
      const signal = point.signal;
      if (signal && signal !== previous && signalMap[signal]) {
        const style = signalMap[signal];
        markers.push({
          time: toChartTime(point.time),
          position: style.position,
          color: style.color,
          shape: style.shape,
          text: style.text,
        });
      }
      previous = signal;
    }
  }

  if (overlays.divergenceLabels !== false) {
    const seen = new Set<string>();
    for (const event of visibleDivergences(data, overlays)) {
      const time = toChartTime(event.confirmed_at);
      if (seen.has(event.id) || !candleTimes.has(time)) continue;
      seen.add(event.id);
      const bullish = event.direction === "bullish";
      markers.push({time, position: bullish ? "belowBar" : "aboveBar", color: bullish ? "#22c55e" : "#ef4444",
        shape: bullish ? "arrowUp" : "arrowDown", text: divergenceLabel(event, overlays)});
    }
  }

  if (structure) {
    const seen = new Set<string>();
    for (const event of structure.events) {
      const style = STRUCTURE_MARKERS[event.event_type];
      const time = toChartTime(event.broken_at);
      if (!style || !candleTimes.has(time)) continue;
      const key = `${event.event_type}:${time}`;
      if (seen.has(key)) continue;
      seen.add(key);
      markers.push({
        time,
        position: style.position,
        color: style.color,
        shape: style.shape,
        text: style.text,
      });
    }
  }

  // Gộp các nhãn trùng thời điểm và cùng vị trí để chữ không đè lên nhau.
  const grouped = new Map<string, SeriesMarker<Time>>();
  for (const marker of markers) {
    const key = `${Number(marker.time)}:${marker.position}`;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, { ...marker });
      continue;
    }
    const labels = `${existing.text ?? ""} · ${marker.text ?? ""}`;
    grouped.set(key, { ...existing, text: labels });
  }

  return [...grouped.values()].sort((a, b) => Number(a.time) - Number(b.time));
};

export default function Chart({ data, structure, overlays }: ChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const savedRangeRef = useRef<{ from: number; to: number } | null>(null);
  const lastDataRef = useRef<IndicatorSeriesResponse | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    setHover(null);

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1220" },
        textColor: "#9fb0c7",
        fontSize: 12,
        panes: {
          separatorColor: "#1e293b",
          separatorHoverColor: "rgba(148, 163, 184, 0.18)",
          enableResize: true,
        },
      },
      grid: {
        vertLines: { color: "rgba(30, 41, 59, 0.55)" },
        horzLines: { color: "rgba(30, 41, 59, 0.55)" },
      },
      rightPriceScale: { borderColor: "#1e293b" },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: false,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) =>
          axisDateFormatter.format(new Date(Number(time) * 1000)),
      },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "vi-VN" },
    });

    const candleTimes = new Set<number>();
    const candleByTime = new Map<number, CandleOut>();
    const candleData: CandlestickData<Time>[] = [];
    const volumeData: { time: UTCTimestamp; value: number; color: string }[] = [];

    for (const candle of data.candles) {
      const time = toChartTime(candle.time);
      candleTimes.add(time);
      candleByTime.set(time, candle);
      candleData.push({
        time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      });
      volumeData.push({
        time,
        value: candle.volume,
        color: candle.close >= candle.open ? "rgba(34, 197, 94, 0.55)" : "rgba(239, 68, 68, 0.55)",
      });
    }

    const candleSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
        priceFormat: { type: "price", precision: 0, minMove: 1 },
      },
      0,
    );
    candleSeries.setData(candleData);

    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1,
    );
    volumeSeries.setData(volumeData);

    const pointsBySeries = new Map<string, Map<number, IndicatorPoint>>();
    for (const series of data.series) {
      const points = new Map<number, IndicatorPoint>();
      for (const point of series.points) points.set(toChartTime(point.time), point);
      pointsBySeries.set(series.key, points);
    }

    const addLine = (
      series: IndicatorSeries,
      key: string,
      options: { color: string; width?: 1 | 2 | 3; dashed?: boolean; pane?: number },
    ) => {
      const line = chart.addSeries(
        LineSeries,
        {
          color: options.color,
          lineWidth: options.width ?? 2,
          lineStyle: options.dashed ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
        },
        options.pane ?? 0,
      );
      line.setData(
        series.points
          .map((point) => ({ time: toChartTime(point.time), value: point.values[key] }))
          .filter(
            (point): point is { time: UTCTimestamp; value: number } =>
              point.value !== null && point.value !== undefined,
          ),
      );
      return line;
    };

    let smaIndex = 0;
    let emaIndex = 0;
    for (const series of data.series) {
      if (series.indicator === "sma" && overlays.sma) {
        addLine(series, "sma", {
          color: SMA_COLORS[smaIndex % SMA_COLORS.length],
          width: 2,
        });
        smaIndex += 1;
      } else if (series.indicator === "ema" && overlays.ema) {
        addLine(series, "ema", {
          color: EMA_COLORS[emaIndex % EMA_COLORS.length],
          width: 2,
        });
        emaIndex += 1;
      } else if (series.indicator === "bollinger" && overlays.bollinger) {
        for (const key of ["upper", "middle", "lower"]) {
          addLine(series, key, {
            color: key === "middle" ? "#cbd5e1" : "#64748b",
            width: 1,
            dashed: key === "middle",
          });
        }
      }
    }

    let nextPane = 2;
    const volumeSma = data.series.find((series) => series.indicator === "volume_sma");
    if (volumeSma) {
      addLine(volumeSma, "volume_sma", { color: "#facc15", width: 1, pane: 1 });
    }

    const rsiSeries = data.series.find((series) => series.indicator === "rsi");
    const rsiPane = rsiSeries ? nextPane++ : null;
    if (rsiSeries) {
      const rsiLine = chart.addSeries(
        LineSeries,
        {
          color: "#38bdf8",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: { type: "price", precision: 1, minMove: 0.1 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        },
        rsiPane ?? 2,
      );
      rsiLine.setData(
        rsiSeries.points
          .map((point) => ({ time: toChartTime(point.time), value: point.values.rsi }))
          .filter(
            (point): point is { time: UTCTimestamp; value: number } =>
              point.value !== null && point.value !== undefined,
          ),
      );
      rsiLine.createPriceLine({
        price: 70,
        color: "rgba(248, 113, 113, 0.55)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "70",
      });
      rsiLine.createPriceLine({
        price: 30,
        color: "rgba(56, 189, 248, 0.55)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "30",
      });
    }

    const macdSeries = data.series.find((series) => series.indicator === "macd");
    const macdPane = macdSeries ? nextPane++ : null;
    if (macdSeries) {
      const macdLine = chart.addSeries(
        LineSeries,
        {
          color: "#38bdf8",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: { type: "price", precision: 0, minMove: 1 },
        },
        macdPane ?? 3,
      );
      macdLine.setData(
        macdSeries.points
          .map((point) => ({ time: toChartTime(point.time), value: point.values.macd }))
          .filter(
            (point): point is { time: UTCTimestamp; value: number } =>
              point.value !== null && point.value !== undefined,
          ),
      );

      const signalLine = chart.addSeries(
        LineSeries,
        {
          color: "#f97316",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: { type: "price", precision: 0, minMove: 1 },
        },
        macdPane ?? 3,
      );
      signalLine.setData(
        macdSeries.points
          .map((point) => ({ time: toChartTime(point.time), value: point.values.signal_line }))
          .filter(
            (point): point is { time: UTCTimestamp; value: number } =>
              point.value !== null && point.value !== undefined,
          ),
      );

      const histogram = chart.addSeries(
        HistogramSeries,
        {
          priceLineVisible: false,
          lastValueVisible: false,
          priceFormat: { type: "price", precision: 0, minMove: 1 },
        },
        macdPane ?? 3,
      );
      histogram.setData(
        macdSeries.points
          .map((point) => ({
            time: toChartTime(point.time),
            value: point.values.histogram,
            color:
              (point.values.histogram ?? 0) >= 0
                ? "rgba(34, 197, 94, 0.55)"
                : "rgba(239, 68, 68, 0.55)",
          }))
          .filter(
            (point): point is { time: UTCTimestamp; value: number; color: string } =>
              point.value !== null && point.value !== undefined,
          ),
      );
    }

    if (overlays.divergenceLines !== false) {
      const seen = new Set<string>();
      for (const event of visibleDivergences(data, overlays)) {
        if (seen.has(event.id)) continue;
        seen.add(event.id);
        const color = event.direction === "bullish" ? "#22c55e" : "#ef4444";
        const drawSegment = (start: string, first: number, end: string, second: number, pane: number) => {
          if (!candleTimes.has(toChartTime(start)) || !candleTimes.has(toChartTime(end))) return;
          const line = chart.addSeries(LineSeries, {color, lineWidth: 2, lineStyle: LineStyle.Dashed,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false}, pane);
          line.setData([{time: toChartTime(start), value: first}, {time: toChartTime(end), value: second}]);
        };
        // Historical geometry is intentionally distinct from the confirmation-bar marker.
        drawSegment(event.start_time, event.start_price, event.end_time, event.end_price, 0);
        // One price segment only, as requested. Oscillator pivot evidence is retained in the API.

      }
    }

    const markers = buildMarkers(data, structure, candleTimes, overlays);
    if (markers.length > 0) {
      createSeriesMarkers(candleSeries, markers);
    }

    const paneWeights: Record<number, number> = { 0: 0.56, 1: 0.12 };
    if (rsiPane !== null) paneWeights[rsiPane] = 0.16;
    if (macdPane !== null) paneWeights[macdPane] = 0.16;
    chart.panes().forEach((pane, index) => {
      pane.setStretchFactor(paneWeights[index] ?? 0.16);
    });

    const total = candleData.length;
    if (total > 0) {
      if (lastDataRef.current === data && savedRangeRef.current) {
        chart.timeScale().setVisibleLogicalRange(savedRangeRef.current);
      } else {
        chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, total - 120), to: total + 1 });
      }
    }
    lastDataRef.current = data;

    const handleCrosshair = (param: MouseEventParams<Time>) => {
      if (!param.time) {
        setHover(null);
        return;
      }
      const time = Number(param.time);
      const candle = candleByTime.get(time);
      if (!candle) {
        setHover(null);
        return;
      }

      const rows: HoverRow[] = [];
      const signals: { label: string; tone: "up" | "down" | "flat" }[] = [];
      for (const series of data.series) {
        const point = pointsBySeries.get(series.key)?.get(time);
        if (!point) continue;
        if (series.indicator === "divergence") {
          const confirmedEvents = visibleDivergences(data, overlays).filter(event => toChartTime(event.confirmed_at) === time);
          const active = confirmedEvents.length ? confirmedEvents.map(event => divergenceLabel(event, overlays)
            + " · swing " + axisDateFormatter.format(new Date(event.end_time)) + " · đã xác nhận") : series.events === undefined && overlays.divergence ? activeDivergences(point.values) : [];
          if (active.length > 0) {
            rows.push({
              label: "Phân kỳ",
              value: active.join(", "),
              color: colorFor(series, "bearish_regular"),
            });
          }
          continue;
        }
        for (const key of hoverKeys(series)) {
          const value = point.values[key];
          if (value === null || value === undefined) continue;
          rows.push({
            label: hoverLabel(series, key),
            value: series.indicator === "volume_sma" ? formatCompact(value) : formatPrice(value),
            color: colorFor(series, key),
          });
        }
        if (point.signal) {
          signals.push({ label: signalLabel(point.signal), tone: signalTone(point.signal) });
        }
      }

      setHover({
        time: candle.time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        rows,
        signals,
      });
    };

    chart.subscribeCrosshairMove(handleCrosshair);

    return () => {
      savedRangeRef.current = chart.timeScale().getVisibleLogicalRange();
      chart.remove();
    };
  }, [data, structure, overlays]);

  const closeTone = hover ? (hover.close >= hover.open ? "up" : "down") : "flat";

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" />
      <div className="chart-legend">
        {hover ? (
          <>
            <div className="legend-title">
              <span>{new Date(hover.time).toLocaleDateString("vi-VN", { timeZone: MARKET_TIME_ZONE })}</span>
              <span className={`legend-close ${closeTone}`}>{formatPrice(hover.close)}</span>
            </div>
            <div className="legend-ohlc">
              <span>Mở {formatPrice(hover.open)}</span>
              <span>Cao {formatPrice(hover.high)}</span>
              <span>Thấp {formatPrice(hover.low)}</span>
              <span>KL {formatCompact(hover.volume)}</span>
            </div>
            {hover.rows.length > 0 && (
              <div className="legend-rows">
                {hover.rows.map((row) => (
                  <span key={`${row.label}`} className="legend-row">
                    <i style={{ background: row.color }} />
                    {row.label}: {row.value}
                  </span>
                ))}
              </div>
            )}
            {hover.signals.length > 0 && (
              <div className="legend-signals">
                {hover.signals.map((signal) => (
                  <span key={signal.label} className={`badge ${signal.tone}`}>
                    {signal.label}
                  </span>
                ))}
              </div>
            )}
          </>
        ) : (
          <span className="legend-hint">Di chuột lên biểu đồ để xem giá trị nến và chỉ báo.</span>
        )}
      </div>
    </div>
  );
}
