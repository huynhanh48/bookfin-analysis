import { IndicatorSeries, registerIndicator, type KLineData } from "klinecharts";
import { findSeries } from "../seriesStore";
import { drawBoundary, fillZone, type ZonePoint } from "./zones";

interface DmiBar {
  pdi?: number;
  mdi?: number;
  adx?: number;
}

const PDI_COLOR = "#26a69a";
const MDI_COLOR = "#ef5350";
const ADX_COLOR = "#f9a825";
// Hai vùng cảnh báo của DMI: xu hướng mạnh trên 50, xu hướng yếu dưới 10.
const STRONG_ZONE = 50;
const WEAK_ZONE = 10;
const PDI_FILL = "rgba(38, 166, 154, 0.16)";
const MDI_FILL = "rgba(239, 83, 80, 0.16)";

function dmiSeries(candles: KLineData[]): DmiBar[] {
  const payload = findSeries(candles);
  return candles.map((bar) => {
    const values = payload?.dmi.get(bar.timestamp);
    if (!values) return {};
    return { pdi: values.plusDi, mdi: values.minusDi, adx: values.adx };
  });
}

/**
 * DMI (+DI, -DI, ADX) lấy từ BE kèm hai vùng tô 50 và 10.
 *
 * +DI/-DI vượt 50 tô phần nằm trên mốc 50; xuống dưới 10 tô phần nằm dưới mốc
 * 10. Cùng quy tắc dừng tô như RSI khi đường quay lại vùng bình thường.
 */
export function registerDmi(): void {
  registerIndicator<DmiBar>({
    name: "DMI",
    shortName: "DMI",
    series: IndicatorSeries.Normal,
    calcParams: [14],
    precision: 2,
    shouldOhlc: false,
    figures: [
      { key: "pdi", title: "PDI: ", type: "line", styles: () => ({ color: PDI_COLOR }) },
      { key: "mdi", title: "MDI: ", type: "line", styles: () => ({ color: MDI_COLOR }) },
      { key: "adx", title: "ADX: ", type: "line", styles: () => ({ color: ADX_COLOR }) },
    ],
    calc: (dataList) => dmiSeries(dataList),
    draw: ({ ctx, indicator, xAxis, yAxis, bounding, visibleRange }) => {
      const result = (indicator.result ?? []) as DmiBar[];
      if (result.length === 0) return false;

      const from = Math.max(visibleRange.from - 1, 0);
      const to = Math.min(visibleRange.to, result.length - 1);
      const axes = { xAxis, yAxis };

      ctx.save();
      // Toạ độ trục theo pane, còn bounding theo container: clip theo gốc pane.
      ctx.beginPath();
      ctx.rect(0, 0, bounding.width, bounding.height);
      ctx.clip();

      const bounds = { left: 0, right: bounding.width };
      drawBoundary(ctx, STRONG_ZONE, axes, bounds);
      drawBoundary(ctx, WEAK_ZONE, axes, bounds);

      const pdiPoints: ZonePoint[] = [];
      const mdiPoints: ZonePoint[] = [];
      for (let index = from; index <= to; index += 1) {
        pdiPoints.push({ index, value: result[index]?.pdi ?? null });
        mdiPoints.push({ index, value: result[index]?.mdi ?? null });
      }
      fillZone(ctx, pdiPoints, STRONG_ZONE, axes, PDI_FILL, 1);
      fillZone(ctx, mdiPoints, STRONG_ZONE, axes, MDI_FILL, 1);
      fillZone(ctx, pdiPoints, WEAK_ZONE, axes, PDI_FILL, -1);
      fillZone(ctx, mdiPoints, WEAK_ZONE, axes, MDI_FILL, -1);

      ctx.restore();
      return false;
    },
  });
}
