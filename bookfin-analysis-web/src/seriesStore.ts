import type { KLineData } from "klinecharts";

/** Giá trị Ichimoku của một nến, do BE tính bằng TA-Lib. */
export interface IchimokuValues {
  tenkan: number | null;
  kijun: number | null;
  senkouA: number | null;
  senkouB: number | null;
}

/** MACD của một nến: DIFF, DEA và histogram. */
export interface MacdValues {
  dif: number | null;
  dea: number | null;
  hist: number | null;
}

/** Sự kiện phân kỳ đã xác nhận, kèm hai pivot để vẽ đường nối. */
export interface DivergenceEvent {
  id: string;
  kind: string;
  direction: string;
  startTime: number;
  endTime: number;
  confirmedAt: number;
  startPrice: number;
  endPrice: number;
  label: string;
}

/** Phân kỳ cấu trúc: gộp nhiều local liên tiếp, vẽ nét chấm. */
export interface StructuralEvent {
  id: string;
  kind: string;
  direction: string;
  indicator: string;
  startTime: number;
  endTime: number;
  confirmedAt: number;
  startPrice: number;
  endPrice: number;
  pivotCount: number;
  memberIds: string[];
  label: string;
}

/** Dữ liệu một lần tải: nến và các chỉ báo BE trả kèm. */
export interface SeriesPayload {
  key: string;
  timeframe: string;
  candles: KLineData[];
  ichimoku: Map<number, IchimokuValues>;
  macd: Map<number, MacdValues>;
  /** RSI(14) theo timestamp. */
  rsi: Map<number, number>;
  /** DMI: +DI, -DI và ADX theo timestamp. */
  dmi: Map<number, { plusDi: number; minusDi: number; adx: number }>;
  divergenceEvents: DivergenceEvent[];
  structuralEvents: StructuralEvent[];
  first: number;
  last: number;
  /** Các timestamp đã có nến trong payload, dùng để khớp nhanh với dataList. */
  timestamps: Set<number>;
  /** Các bar chỉ có giá trị tạm tính từ realtime (chưa đóng nến). */
  provisionalTimestamps: Set<number>;
}

const cache = new Map<string, SeriesPayload>();

/**
 * Payload của mã/khung đang hiển thị.
 *
 * Nhiều mã có cùng mốc thời gian (cùng phiên), nên không thể chọn payload chỉ
 * dựa trên timestamp. Payload mới nhất do datafeed nạp chính là dữ liệu đang vẽ.
 */
let activeKey: string | null = null;

/**
 * Lưu payload theo `ticker:timeframe` và đánh dấu là dữ liệu đang hiển thị.
 *
 * Chart nạp theo cửa sổ nên mỗi lần tải chỉ phủ một đoạn; gộp dần vào payload
 * cũ để indicator tra cứu được cả khi người dùng cuộn về quá khứ.
 */
export function putSeries(payload: SeriesPayload): void {
  const existing = cache.get(payload.key);
  activeKey = payload.key;
  if (!existing) {
    payload.timestamps = new Set(payload.candles.map((bar) => bar.timestamp));
    payload.provisionalTimestamps = payload.provisionalTimestamps ?? new Set();
    cache.set(payload.key, payload);
    return;
  }

  const byTimestamp = new Map<number, KLineData>();
  for (const candle of [...existing.candles, ...payload.candles]) {
    byTimestamp.set(candle.timestamp, candle);
  }
  const candles = [...byTimestamp.values()].sort((left, right) => left.timestamp - right.timestamp);

  const merged: SeriesPayload = {
    key: payload.key,
    timeframe: payload.timeframe,
    candles,
    ichimoku: new Map([...existing.ichimoku, ...payload.ichimoku]),
    macd: new Map([...existing.macd, ...payload.macd]),
    rsi: new Map([...existing.rsi, ...payload.rsi]),
    dmi: new Map([...existing.dmi, ...payload.dmi]),
    divergenceEvents: [
      ...new Map(
        [...existing.divergenceEvents, ...payload.divergenceEvents].map((event) => [
          `${event.startTime}:${event.endTime}:${event.direction}:${event.kind}`,
          event,
        ]),
      ).values(),
    ],
    structuralEvents: [
      ...new Map(
        [...existing.structuralEvents, ...payload.structuralEvents].map((event) => [event.id, event]),
      ).values(),
    ],
    first: candles[0].timestamp,
    last: candles[candles.length - 1].timestamp,
    timestamps: new Set(candles.map((bar) => bar.timestamp)),
    provisionalTimestamps: new Set(existing.provisionalTimestamps),
  };
  cache.set(payload.key, merged);
}

/** Payload đã gộp của một mã/khung, dùng để báo tổng số nến đã nạp. */
export function getSeries(key: string): SeriesPayload | undefined {
  return cache.get(key);
}

/** Xoá toàn bộ cache; dùng khi cần buộc tải lại từ BE. */
export function clearSeries(): void {
  cache.clear();
  activeKey = null;
}

/**
 * Payload có bao được bộ nến đang hiển thị không.
 *
 * Chart realtime có thể có thêm bar đang chạy (mới hơn nến đã đóng), nên chỉ so
 * khớp tới nến đóng cuối cùng mà payload phủ được.
 */
function covers(payload: SeriesPayload, candles: KLineData[]): boolean {
  if (candles.length === 0) return false;
  const first = candles[0].timestamp;
  if (payload.first > first) return false;

  let lastClosed: number | null = null;
  for (let index = candles.length - 1; index >= 0; index -= 1) {
    if (candles[index].timestamp <= payload.last) {
      lastClosed = candles[index].timestamp;
      break;
    }
  }
  if (lastClosed === null) return false;
  return payload.timestamps.has(first) && payload.timestamps.has(lastClosed);
}

/**
 * Tìm payload của bộ nến chart đang hiển thị.
 *
 * Indicator của KLineChart không nhận `symbol`/`period`, nên phải suy ra từ dữ
 * liệu: ưu tiên payload vừa được datafeed nạp, chỉ khi nó không bao phủ dữ liệu
 * (ví dụ sau khi nạp thêm nến cũ) mới dò các payload khác.
 */
export function findSeries(candles: KLineData[]): SeriesPayload | null {
  if (candles.length === 0) return null;

  const active = activeKey ? cache.get(activeKey) : undefined;
  if (active && covers(active, candles)) return active;

  const first = candles[0].timestamp;
  const last = candles[candles.length - 1].timestamp;
  for (const payload of cache.values()) {
    if (payload.first === first && payload.last === last) return payload;
  }
  for (const payload of cache.values()) {
    if (covers(payload, candles)) return payload;
  }
  return null;
}


interface ProvisionalIndicators {
  ichimoku?: Record<string, number | null>;
  macd?: Record<string, number | null>;
  rsi?: Record<string, number | null>;
  adx?: Record<string, number | null>;
}

function toNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Đánh dấu bar đã đóng: giá trị chỉ báo chuyển từ tạm tính sang chính thức. */
export function markConfirmed(key: string, timestamp: number): void {
  cache.get(key)?.provisionalTimestamps.delete(timestamp);
}

/**
 * Ghi giá trị chỉ báo tạm tính cho nến đang chạy vào payload đã tải.
 *
 * Chỉ dùng để hiển thị; khi nến đóng, BE trả series chính thức và các giá trị này
 * bị thay thế. Bar tạm của ngày cũ được xoá khi sang bar mới.
 */
export function applyProvisionalIndicators(
  key: string,
  timestamp: number,
  indicators: ProvisionalIndicators,
): void {
  const payload = cache.get(key);
  if (!payload) return;

  for (const previous of [...payload.provisionalTimestamps]) {
    if (previous === timestamp) continue;
    payload.ichimoku.delete(previous);
    payload.macd.delete(previous);
    payload.rsi.delete(previous);
    payload.dmi.delete(previous);
    payload.provisionalTimestamps.delete(previous);
  }
  payload.provisionalTimestamps.add(timestamp);

  if (indicators.ichimoku) {
    payload.ichimoku.set(timestamp, {
      tenkan: toNumber(indicators.ichimoku.tenkan),
      kijun: toNumber(indicators.ichimoku.kijun),
      senkouA: toNumber(indicators.ichimoku.senkou_a),
      senkouB: toNumber(indicators.ichimoku.senkou_b),
    });
  }
  if (indicators.macd) {
    payload.macd.set(timestamp, {
      dif: toNumber(indicators.macd.macd),
      dea: toNumber(indicators.macd.signal_line),
      hist: toNumber(indicators.macd.histogram),
    });
  }
  if (indicators.rsi) {
    const value = toNumber(indicators.rsi.rsi);
    if (value !== null) payload.rsi.set(timestamp, value);
  }
  if (indicators.adx) {
    const plusDi = toNumber(indicators.adx.plus_di);
    const minusDi = toNumber(indicators.adx.minus_di);
    const adx = toNumber(indicators.adx.adx);
    if (plusDi !== null && minusDi !== null && adx !== null) {
      payload.dmi.set(timestamp, { plusDi, minusDi, adx });
    }
  }
}
