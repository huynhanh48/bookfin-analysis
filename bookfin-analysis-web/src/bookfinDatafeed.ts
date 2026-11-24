import { ChartRealtime } from "./chartRealtime";
import type {
  Datafeed,
  DatafeedSubscribeCallback,
  Period,
  SymbolInfo,
} from "@klinecharts/pro";
import type { KLineData } from "klinecharts";
import {
  getSeries,
  putSeries,
  type DivergenceEvent,
  type StructuralEvent,
  type IchimokuValues,
  type MacdValues,
} from "./seriesStore";

interface CandleDto {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  value?: number | null;
}

interface PointDto {
  time: string;
  values: Record<string, number | null>;
  signal?: string | null;
}

interface EventDto {
  id: string;
  kind: string;
  direction: string;
  start_time: string;
  end_time: string;
  confirmed_at: string;
  start_price: number;
  end_price: number;
  label?: string;
}

interface StructuralDto {
  id: string;
  kind: string;
  direction: string;
  indicator: string;
  start_time: string;
  end_time: string;
  confirmed_at: string;
  start_price: number;
  end_price: number;
  pivot_count: number;
  member_ids: string[];
  label: string;
}

interface SeriesDto {
  count: number;
  candles: CandleDto[];
  series: {
    indicator: string;
    points?: PointDto[];
    events?: EventDto[];
    structural_divergences?: StructuralDto[];
  }[];
  structural_divergences?: StructuralDto[];
}

interface SymbolListDto {
  items: { symbol: string; exchange: string | null; company_name: string | null; active: boolean }[];
}

interface PeriodSpec {
  timeframe: "1D" | "1W";
  limit: number;
}

const PERIODS: Record<string, PeriodSpec> = {
  // Mỗi cửa sổ của Pro là 500 đơn vị thời gian, nên 400 nến là đủ và giữ payload nhẹ.
  day: { timeframe: "1D", limit: 400 },
  week: { timeframe: "1W", limit: 400 },
};

// Bộ chỉ báo chart cần; cùng công thức với engine chỉ báo của BE.
const REQUESTED_INDICATORS = [
  "ichimoku",
  "divergence",
  "macd:12:26:9",
  "rsi:14",
  "adx:14",
];

/** Báo trạng thái tải dữ liệu cho UI thay vì im lặng trả chart trống. */
export type StatusHandler = (message: string, level: "ok" | "error") => void;

function periodSpec(period: Period): PeriodSpec | null {
  return PERIODS[period.timespan] ?? null;
}

/** Sắp xếp tăng dần và bỏ nến trùng timestamp để chart luôn nhận dữ liệu hợp lệ. */
function normalize(candles: KLineData[]): KLineData[] {
  const seen = new Set<number>();
  const unique: KLineData[] = [];
  for (const candle of [...candles].sort((left, right) => left.timestamp - right.timestamp)) {
    if (seen.has(candle.timestamp)) continue;
    seen.add(candle.timestamp);
    unique.push(candle);
  }
  return unique;
}

/**
 * Datafeed KLineChart Pro dùng dữ liệu BookFin BE.
 *
 * Mỗi lần tải chỉ gọi một endpoint: nến, Ichimoku, MACD, RSI, DMI và phân kỳ
 * được BE trả về cùng một payload. Pro gọi `getHistoryKLineData` theo cửa sổ thời
 * gian (`from`/`to`) rồi `applyNewData`/`applyMoreData`, nên phải trả đúng cửa sổ
 * đó — trả toàn bộ lịch sử mỗi lần sẽ làm chart ghép trùng dữ liệu.
 */
export class BookfinDatafeed implements Datafeed {
  private readonly realtime = new ChartRealtime((message, level) => this.onStatus?.(message, level));
  private activeChannel: string | null = null;
  constructor(
    private readonly onStatus?: StatusHandler,
    /** Gọi sau khi BE trả dữ liệu, để app khôi phục nét vẽ đã lưu. */
    private readonly onLoaded?: () => void,
  ) {}

  async searchSymbols(search = ""): Promise<SymbolInfo[]> {
    // Tìm phía BE để không bị giới hạn trang: danh mục có hơn 500 mã nên lọc
    // ở client trên một trang sẽ làm mất các mã như VIC.
    const term = search.trim().toUpperCase();
    const params = new URLSearchParams({ limit: "1000" });
    if (term) params.set("q", term);
    const response = await fetch(`/api/v1/market/symbols?${params.toString()}`);
    if (!response.ok) return [];
    const body = (await response.json()) as SymbolListDto;
    return body.items
      .filter((item) => !term || item.symbol.includes(term) || (item.company_name ?? "").toUpperCase().includes(term))
      .sort((left, right) => {
        // Mã khớp chính xác hoặc bắt đầu bằng từ khoá lên trước.
        const rank = (symbol: string) =>
          symbol === term ? 0 : symbol.startsWith(term) ? 1 : 2;
        return rank(left.symbol) - rank(right.symbol) || left.symbol.localeCompare(right.symbol);
      })
      .map((item) => ({
        ticker: item.symbol,
        name: item.company_name ?? item.symbol,
        shortName: item.symbol,
        exchange: item.exchange ?? "HOSE",
        market: "stocks",
        priceCurrency: "nghìn đồng",
        pricePrecision: 2,
        volumePrecision: 0,
      }));
  }

  async getHistoryKLineData(
    symbol: SymbolInfo,
    period: Period,
    from: number,
    to: number,
  ): Promise<KLineData[]> {
    const spec = periodSpec(period);
    const ticker = symbol.ticker.toUpperCase();
    if (!spec) {
      this.onStatus?.(`Khung ${period.text} chưa được hỗ trợ.`, "error");
      return [];
    }

    const query = new URLSearchParams({
      timeframe: spec.timeframe,
      limit: String(spec.limit),
    });
    // KLineChart Pro 0.1.x computes the weekly loadMore `to` from epoch
    // (1970-01-08) instead of the current window. Fall back to the first
    // candle already loaded for the same symbol/timeframe.
    const seriesKey = `${ticker}:${spec.timeframe}`;
    const existing = getSeries(seriesKey);
    const periodMs = spec.timeframe === "1W" ? 7 * 24 * 60 * 60 * 1000 : 24 * 60 * 60 * 1000;
    const plausibleTo =
      Number.isFinite(to) && to > Date.parse("2000-01-01") && to <= Date.now() + periodMs;
    const fallbackTo =
      existing && existing.candles.length > 0
        ? existing.candles[0].timestamp - periodMs
        : null;
    const requestTo = plausibleTo ? to : fallbackTo;
    // Lần nạp đầu (`to` gần hiện tại) bỏ `end` để BE ghép luôn nến đang chạy.
    const isInitialLoad = requestTo === null || requestTo > Date.now() - 6 * 60 * 60 * 1000;
    if (!isInitialLoad && requestTo !== null) {
      query.set("end", new Date(requestTo).toISOString());
    }
    query.set("include_provisional", "1");
    for (const indicator of REQUESTED_INDICATORS) {
      query.append("indicator", indicator);
    }

    try {
      const response = await fetch(
        `/api/v1/indicators/${encodeURIComponent(ticker)}/series?${query.toString()}`,
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        // Cuộn quá đầu lịch sử: BE báo `insufficient_data`, coi như hết dữ liệu
        // thay vì hiển thị lỗi.
        if (detail?.error?.code === "insufficient_data") return [];
        this.onStatus?.(
          detail?.error?.message ?? `Không tải được dữ liệu ${ticker} ${spec.timeframe}.`,
          "error",
        );
        return [];
      }

      const body = (await response.json()) as SeriesDto;
      const candles = normalize(
        body.candles.map((candle) => ({
          timestamp: Date.parse(candle.time),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          turnover: candle.value ?? undefined,
        })),
      );

      if (candles.length === 0) {
        this.onStatus?.(`${ticker} chưa có nến ${spec.timeframe} đã đóng.`, "error");
        return [];
      }

      const ichimoku = new Map<number, IchimokuValues>();
      const macd = new Map<number, MacdValues>();
      const rsi = new Map<number, number>();
      const dmi = new Map<number, { plusDi: number; minusDi: number; adx: number }>();
      let divergenceEvents: DivergenceEvent[] = [];
      let structuralEvents: StructuralEvent[] = [];

      for (const series of body.series) {
        for (const point of series.points ?? []) {
          const timestamp = Date.parse(point.time);
          const values = point.values;
          switch (series.indicator) {
            case "ichimoku":
              ichimoku.set(timestamp, {
                tenkan: values.tenkan ?? null,
                kijun: values.kijun ?? null,
                senkouA: values.senkou_a ?? null,
                senkouB: values.senkou_b ?? null,
              });
              break;
            case "macd":
              macd.set(timestamp, {
                dif: values.macd ?? null,
                dea: values.signal_line ?? null,
                hist: values.histogram ?? null,
              });
              break;
            case "rsi":
              if (values.rsi != null) rsi.set(timestamp, values.rsi);
              break;
            case "adx":
              if (values.plus_di != null && values.minus_di != null && values.adx != null) {
                dmi.set(timestamp, {
                  plusDi: values.plus_di,
                  minusDi: values.minus_di,
                  adx: values.adx,
                });
              }
              break;
            default:
              break;
          }
        }
        if (series.indicator === "divergence") {
          structuralEvents = (
            series.structural_divergences ??
            body.structural_divergences ??
            []
          ).map((event) => ({
            id: event.id,
            kind: event.kind,
            direction: event.direction,
            indicator: event.indicator,
            startTime: Date.parse(event.start_time),
            endTime: Date.parse(event.end_time),
            confirmedAt: Date.parse(event.confirmed_at),
            startPrice: event.start_price,
            endPrice: event.end_price,
            pivotCount: event.pivot_count,
            memberIds: event.member_ids ?? [],
            label: event.label ?? "STR",
          }));
          divergenceEvents = (series.events ?? []).map((event) => ({
            id: event.id,
            kind: event.kind,
            direction: event.direction,
            startTime: Date.parse(event.start_time),
            endTime: Date.parse(event.end_time),
            confirmedAt: Date.parse(event.confirmed_at),
            startPrice: event.start_price,
            endPrice: event.end_price,
            label: event.label ?? "",
          }));
        }
      }

      const key = `${ticker}:${spec.timeframe}`;
      putSeries({
        key,
        timeframe: spec.timeframe,
        candles,
        ichimoku,
        macd,
        rsi,
        dmi,
        divergenceEvents,
        structuralEvents,
        first: candles[0].timestamp,
        last: candles[candles.length - 1].timestamp,
        timestamps: new Set(candles.map((bar) => bar.timestamp)),
        provisionalTimestamps: new Set<number>(),
      });

      // Pro nạp theo cửa sổ: chỉ trả nến nằm trong [from, to] để `applyMoreData`
      // không ghép trùng dữ liệu khi cuộn hoặc đổi mã.
      const plausibleFrom =
        Number.isFinite(from) && from > Date.parse("2000-01-01");
      const lower = plausibleFrom
        ? from
        : requestTo !== null
          ? requestTo - 500 * periodMs
          : Number.NEGATIVE_INFINITY;
      const upper = requestTo !== null ? requestTo : Number.POSITIVE_INFINITY;
      const window = candles.filter(
        (candle) => candle.timestamp >= lower && candle.timestamp <= upper,
      );

      this.onLoaded?.();
      // Báo tổng số đã nạp (đã gộp nhiều cửa sổ) thay vì số nến của riêng cửa sổ này.
      const merged = getSeries(key) ?? { candles, divergenceEvents, structuralEvents };
      this.onStatus?.(
        `${ticker} · ${spec.timeframe} · ${merged.candles.length} nến · ` +
          `${merged.divergenceEvents.length} local · ${merged.structuralEvents.length} cấu trúc`,
        "ok",
      );
      return window;
    } catch (error) {
      this.onStatus?.(
        `Lỗi tải ${ticker}: ${error instanceof Error ? error.message : String(error)}`,
        "error",
      );
      return [];
    }
  }

  subscribe(symbol: SymbolInfo, period: Period, callback: DatafeedSubscribeCallback): void {
    const spec = periodSpec(period);
    if (!spec) return;
    const ticker = symbol.ticker.toUpperCase();
    this.activeChannel = `market:${ticker}:${spec.timeframe}`;
    this.realtime.subscribe(this.activeChannel, callback, getSeries(`${ticker}:${spec.timeframe}`)?.last ?? 0);
  }

  unsubscribe(symbol: SymbolInfo, period: Period): void {
    const channel = `market:${symbol.ticker.toUpperCase()}:${periodSpec(period)?.timeframe}`;
    if (channel !== this.activeChannel) return;
    this.close();
  }

  close(): void {
    this.activeChannel = null;
    this.realtime.close();
  }
}
