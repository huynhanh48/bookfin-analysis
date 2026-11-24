import { IndicatorSeries, registerIndicator, type KLineData } from "klinecharts";
import { findSeries } from "../seriesStore";
import { drawBoundary, fillZone, type BoundaryStyle, type ZonePoint } from "./zones";

interface RsiBar {
  rsi?: number;
}

const OVERBOUGHT = 70;
const OVERSOLD = 30;
const MIDLINE = 50;
const OVERBOUGHT_FILL = "rgba(239, 83, 80, 0.16)";
const OVERSOLD_FILL = "rgba(38, 166, 154, 0.16)";

// 70 và 30 dùng chung một màu trung tính như mặc định; mốc 50 tách màu riêng.
const ZONE_STYLE: BoundaryStyle = {
  color: "rgba(120, 130, 145, 0.55)",
  dash: [4, 3],
};
const MIDLINE_STYLE: BoundaryStyle = {
  // Đường RSI mặc định đang màu cam nên mốc 50 dùng xanh dương để tách hẳn.
  color: "rgba(22, 119, 255, 0.6)",
  dash: [3, 3],
};

function rsiSeries(candles: KLineData[]): (number | null)[] {
  const payload = findSeries(candles);
  return candles.map((bar) => payload?.rsi.get(bar.timestamp) ?? null);
}

/**
 * RSI(14) một đường lấy từ BE, kèm ba mốc tham chiếu 70 / 50 / 30.
 *
 * - 70, 30: cùng một màu xám nét đứt, đánh dấu vùng quá mua – quá bán.
 * - 50: màu xanh dương để dễ phân biệt, ranh giới động lượng tăng/giảm.
 * - Tô vùng khi RSI vượt 70 hoặc xuống dưới 30; vùng tô dừng ngay khi đường
 *   quay lại vùng trung tính.
 */
export function registerRsi(): void {
  registerIndicator<RsiBar>({
    name: "RSI",
    shortName: "RSI",
    series: IndicatorSeries.Normal,
    calcParams: [14],
    precision: 2,
    shouldOhlc: false,
    // Dùng style dòng mặc định của chart (mảnh, màu chuẩn) — không override.
    figures: [{ key: "rsi", title: "RSI: ", type: "line" }],
    calc: (dataList) => {
      const values = rsiSeries(dataList);
      return values.map((value) => (value == null ? {} : { rsi: value }));
    },
    draw: ({ ctx, indicator, xAxis, yAxis, bounding, visibleRange }) => {
      const result = (indicator.result ?? []) as RsiBar[];
      if (result.length === 0) return false;

      const from = Math.max(visibleRange.from - 1, 0);
      const to = Math.min(visibleRange.to, result.length - 1);
      const axes = { xAxis, yAxis };
      const bounds = { left: 0, right: bounding.width };

      ctx.save();
      // Toạ độ trục theo pane, còn bounding theo container: clip theo gốc pane.
      ctx.beginPath();
      ctx.rect(0, 0, bounding.width, bounding.height);
      ctx.clip();

      drawBoundary(ctx, OVERBOUGHT, axes, bounds, ZONE_STYLE);
      drawBoundary(ctx, MIDLINE, axes, bounds, MIDLINE_STYLE);
      drawBoundary(ctx, OVERSOLD, axes, bounds, ZONE_STYLE);

      const points: ZonePoint[] = [];
      for (let index = from; index <= to; index += 1) {
        points.push({ index, value: result[index]?.rsi ?? null });
      }
      fillZone(ctx, points, OVERBOUGHT, axes, OVERBOUGHT_FILL, 1);
      fillZone(ctx, points, OVERSOLD, axes, OVERSOLD_FILL, -1);

      ctx.restore();
      // Trả false để KLineChart vẫn vẽ đường RSI.
      return false;
    },
  });
}
