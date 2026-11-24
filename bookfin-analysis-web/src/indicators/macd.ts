import {
  IndicatorSeries,
  PolygonType,
  registerIndicator,
  type KLineData,
} from "klinecharts";
import { findSeries } from "../seriesStore";

interface MacdBar {
  dif?: number;
  dea?: number;
  macd?: number;
  /** 1 = cắt lên (buy), -1 = cắt xuống (sell), 0/undefined = không cắt. */
  cross?: number;
}

const DIF_COLOR = "#f9a825";
const DEA_COLOR = "#1e88e5";
const BUY_COLOR = "#26a69a";
const SELL_COLOR = "#ef5350";

function macdValues(candles: KLineData[]): MacdBar[] {
  const payload = findSeries(candles);
  return candles.map((bar) => {
    const values = payload?.macd.get(bar.timestamp);
    if (!values) return {};
    const line: MacdBar = {};
    if (values.dif != null) line.dif = values.dif;
    if (values.dea != null) line.dea = values.dea;
    if (values.hist != null) line.macd = values.hist;
    return line;
  });
}

/**
 * MACD của BE kèm tín hiệu BUY/SELL khi DIF cắt DEA.
 *
 * Ghi đè indicator `MACD` có sẵn của KLineChart để giữ đúng vị trí pane và
 * tên hiển thị; giá trị lấy từ BE thay vì tính lại trên FE.
 */
export function registerMacd(): void {
  registerIndicator<MacdBar>({
    name: "MACD",
    shortName: "MACD",
    series: IndicatorSeries.Normal,
    calcParams: [12, 26, 9],
    precision: 2,
    shouldOhlc: false,
    figures: [
      {
        key: "dif",
        title: "DIF: ",
        type: "line",
        styles: () => ({ color: DIF_COLOR }),
      },
      {
        key: "dea",
        title: "DEA: ",
        type: "line",
        styles: () => ({ color: DEA_COLOR }),
      },
      {
        key: "macd",
        title: "MACD: ",
        type: "bar",
        baseValue: 0,
        styles: (data, indicator, defaultStyles) => {
          const value = data.current.indicatorData?.macd ?? 0;
          const bars = (indicator.styles?.bars ?? defaultStyles.bars)[0];
          const color = value > 0 ? bars.upColor : value < 0 ? bars.downColor : bars.noChangeColor;
          return { color, borderColor: color, style: PolygonType.Fill };
        },
      },
    ],
    calc: (dataList) => {
      const values = macdValues(dataList);
      return values.map((bar, index) => {
        if (index === 0) return bar;
        const previous = values[index - 1];
        if (
          bar.dif == null ||
          bar.dea == null ||
          previous.dif == null ||
          previous.dea == null
        ) {
          return bar;
        }
        if (previous.dif <= previous.dea && bar.dif > bar.dea) bar.cross = 1;
        else if (previous.dif >= previous.dea && bar.dif < bar.dea) bar.cross = -1;
        return bar;
      });
    },
    draw: ({ ctx, indicator, xAxis, yAxis, bounding, visibleRange }) => {
      const result = (indicator.result ?? []) as MacdBar[];
      if (result.length === 0) return false;

      const from = Math.max(visibleRange.from - 1, 0);
      const to = Math.min(visibleRange.to, result.length - 1);

      ctx.save();
      // Toạ độ trục theo pane, còn bounding theo container: clip theo gốc pane.
      ctx.beginPath();
      ctx.rect(0, 0, bounding.width, bounding.height);
      ctx.clip();
      for (let index = from; index <= to; index += 1) {
        const bar = result[index];
        if (!bar?.cross) continue;
        const x = xAxis.convertToPixel(index);
        const y = yAxis.convertToPixel(bar.dif ?? 0);
        const buy = bar.cross > 0;
        ctx.fillStyle = buy ? BUY_COLOR : SELL_COLOR;
        ctx.beginPath();
        if (buy) {
          // Tam giác hướng lên dưới điểm cắt: tín hiệu mua.
          ctx.moveTo(x, y + 4);
          ctx.lineTo(x - 4.5, y + 11);
          ctx.lineTo(x + 4.5, y + 11);
        } else {
          // Tam giác hướng xuống trên điểm cắt: tín hiệu bán.
          ctx.moveTo(x, y - 4);
          ctx.lineTo(x - 4.5, y - 11);
          ctx.lineTo(x + 4.5, y - 11);
        }
        ctx.closePath();
        ctx.fill();
      }

      ctx.restore();
      // Trả false để KLineChart vẫn vẽ DIF/DEA/MACD.
      return false;
    },
  });
}
