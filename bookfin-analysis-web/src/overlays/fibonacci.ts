import { registerOverlay, utils, type OverlayTemplate } from "klinecharts";

/**
 * Fibonacci retracement có thể bật/tắt từng mức.
 *
 * Ghi đè overlay `fibonacciLine` mặc định của KLineChart 9 để dùng danh sách mức
 * trong cài đặt thay vì bộ cố định [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].
 */

export interface FibLevel {
  /** Tỉ lệ, ví dụ 0.618. */
  ratio: number;
  /** Có vẽ mức này hay không. */
  enabled: boolean;
}

/** Các mức mặc định, giữ đúng bộ của KLineChart. */
export const DEFAULT_FIB_LEVELS: FibLevel[] = [
  { ratio: 1, enabled: true },
  { ratio: 0.786, enabled: true },
  { ratio: 0.618, enabled: true },
  { ratio: 0.5, enabled: true },
  { ratio: 0.382, enabled: true },
  { ratio: 0.236, enabled: true },
  { ratio: 0, enabled: true },
];

let levels: FibLevel[] = DEFAULT_FIB_LEVELS.map((level) => ({ ...level }));

/** Danh sách mức hiện tại (bản sao để tránh sửa ngoài ý muốn). */
export function fibLevels(): FibLevel[] {
  return levels.map((level) => ({ ...level }));
}

/** Thay danh sách mức; overlay đang vẽ sẽ dùng danh sách mới ở lần render kế tiếp. */
export function setFibLevels(next: ReadonlyArray<FibLevel>): void {
  const valid = next.filter((level) => Number.isFinite(level.ratio));
  levels = valid.length > 0 ? valid.map((level) => ({ ...level })) : levels;
}

/** Các mức đang bật, sắp xếp giảm dần để vẽ từ trên xuống như mặc định. */
export function enabledFibRatios(): number[] {
  return levels
    .filter((level) => level.enabled)
    .map((level) => level.ratio)
    .sort((left, right) => right - left);
}

const fibonacciRetracement: OverlayTemplate = {
  name: "fibonacciLine",
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, bounding, overlay, precision, yAxis }) => {
    const points = overlay.points;
    if (coordinates.length === 0) return [];

    const isInCandle = yAxis?.isInCandle() ?? true;
    const digits = isInCandle ? precision.price : precision.excludePriceVolumeMax;
    const lines: Array<{ coordinates: Array<{ x: number; y: number }> }> = [];
    const texts: Array<{ x: number; y: number; text: string; baseline: string }> = [];

    const startValue = points[0]?.value;
    const endValue = points[1]?.value;

    if (coordinates.length > 1 && typeof startValue === "number" && typeof endValue === "number") {
      const yDiff = coordinates[0].y - coordinates[1].y;
      const valueDiff = startValue - endValue;
      for (const ratio of enabledFibRatios()) {
        const y = coordinates[1].y + yDiff * ratio;
        const value = endValue + valueDiff * ratio;
        lines.push({ coordinates: [{ x: 0, y }, { x: bounding.width, y }] });
        texts.push({
          x: 0,
          y,
          text: `${utils.formatThousands(utils.formatPrecision(value, digits), ",")} (${(ratio * 100).toFixed(1)}%)`,
          baseline: "bottom",
        });
      }
    }

    return [
      { type: "line", attrs: lines },
      { type: "text", ignoreEvent: true, attrs: texts },
    ];
  },
};

/** Đăng ký Fibonacci retracement; gọi trước khi tạo chart. */
export function registerFibonacci(): void {
  registerOverlay(fibonacciRetracement);
}
