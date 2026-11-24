import {
  IndicatorSeries,
  registerIndicator,
  type IndicatorDrawParams,
  type KLineData,
} from "klinecharts";
import { ichimokuSettings } from "../indicatorState";
import { findSeries, type IchimokuValues } from "../seriesStore";

interface IchimokuBar {
  tenkan?: number;
  kijun?: number;
  senkouA?: number;
  senkouB?: number;
}

const TENKAN_COLOR = "#f9a825";
const KIJUN_COLOR = "#1e88e5";
const SENKOU_A_COLOR = "#2e7d32";
const SENKOU_B_COLOR = "#c62828";
const CHIKOU_COLOR = "#8e24aa";
const CLOUD_UP_COLOR = "rgba(46, 125, 50, 0.14)";
const CLOUD_DOWN_COLOR = "rgba(198, 40, 40, 0.14)";

/**
 * Độ dời chuẩn của Ichimoku: Senkou Span vẽ trước giá 26 nến, Chikou lùi 26 nến.
 *
 * TA-Lib trả giá trị tại đúng nến tính (không dời). Việc dời là quy ước hiển thị
 * của Ichimoku Kinko Hyo, thực hiện ở lớp vẽ nên giá trị gốc vẫn khớp BE.
 */
const KIJUN_SHIFT = 26;

function seriesOf(candles: KLineData[]): IchimokuValues[] {
  const payload = findSeries(candles);
  return candles.map(
    (bar) =>
      payload?.ichimoku.get(bar.timestamp) ?? {
        tenkan: null,
        kijun: null,
        senkouA: null,
        senkouB: null,
      },
  );
}

function drawPolyline(
  ctx: CanvasRenderingContext2D,
  values: (number | null)[],
  offset: number,
  color: string,
  params: IndicatorDrawParams<IchimokuBar>,
): void {
  const { xAxis, yAxis } = params;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  ctx.beginPath();
  let started = false;
  values.forEach((value, index) => {
    if (value == null) {
      started = false;
      return;
    }
    const x = xAxis.convertToPixel(index + offset);
    const y = yAxis.convertToPixel(value);
    if (started) {
      ctx.lineTo(x, y);
    } else {
      ctx.moveTo(x, y);
      started = true;
    }
  });
  ctx.stroke();
}

/** Tô mây Kumo giữa Senkou A và Senkou B, đã dời 26 nến theo đúng thuật toán. */
function drawCloud(
  ctx: CanvasRenderingContext2D,
  values: IchimokuValues[],
  params: IndicatorDrawParams<IchimokuBar>,
): void {
  const { xAxis, yAxis } = params;
  for (let index = 0; index < values.length - 1; index += 1) {
    const current = values[index];
    const next = values[index + 1];
    if (
      current.senkouA == null ||
      current.senkouB == null ||
      next.senkouA == null ||
      next.senkouB == null
    ) {
      continue;
    }
    const x0 = xAxis.convertToPixel(index + KIJUN_SHIFT);
    const x1 = xAxis.convertToPixel(index + 1 + KIJUN_SHIFT);
    const up0 = Math.max(current.senkouA, current.senkouB);
    const up1 = Math.max(next.senkouA, next.senkouB);
    const low0 = Math.min(current.senkouA, current.senkouB);
    const low1 = Math.min(next.senkouA, next.senkouB);

    ctx.fillStyle = current.senkouA >= current.senkouB ? CLOUD_UP_COLOR : CLOUD_DOWN_COLOR;
    ctx.beginPath();
    ctx.moveTo(x0, yAxis.convertToPixel(up0));
    ctx.lineTo(x1, yAxis.convertToPixel(up1));
    ctx.lineTo(x1, yAxis.convertToPixel(low1));
    ctx.lineTo(x0, yAxis.convertToPixel(low0));
    ctx.closePath();
    ctx.fill();
  }
}

/**
 * Ichimoku Kinko Hyo với đầy đủ Tenkan, Kijun, Senkou A/B, Chikou và mây Kumo.
 *
 * Giá trị lấy từ BE (TA-Lib); FE chỉ dời trục theo quy ước hiển thị và bật/tắt
 * từng đường theo cài đặt trong modal Indicator.
 */
export function registerIchimoku(): void {
  registerIndicator<IchimokuBar>({
    name: "ICHIMOKU",
    shortName: "ICHIMOKU",
    series: IndicatorSeries.Price,
    calcParams: [9, 26, 52],
    precision: 2,
    shouldOhlc: false,
    figures: [
      { key: "tenkan", title: "Tenkan: ", type: "line" },
      { key: "kijun", title: "Kijun: ", type: "line" },
      { key: "senkouA", title: "Senkou A: ", type: "line" },
      { key: "senkouB", title: "Senkou B: ", type: "line" },
    ],
    calc: (dataList) => {
      const values = seriesOf(dataList);
      return values.map((value) => {
        const line: IchimokuBar = {};
        if (value.tenkan != null) line.tenkan = value.tenkan;
        if (value.kijun != null) line.kijun = value.kijun;
        if (value.senkouA != null) line.senkouA = value.senkouA;
        if (value.senkouB != null) line.senkouB = value.senkouB;
        return line;
      });
    },
    draw: (params) => {
      const { ctx, kLineDataList, bounding } = params;
      const values = seriesOf(kLineDataList);
      if (values.length === 0) return true;

      ctx.save();
      // Toạ độ trục theo pane, còn bounding theo container: clip theo gốc pane.
      ctx.beginPath();
      ctx.rect(0, 0, bounding.width, bounding.height);
      ctx.clip();

      if (ichimokuSettings.cloud) drawCloud(ctx, values, params);
      if (ichimokuSettings.senkouA) {
        drawPolyline(
          ctx,
          values.map((value) => value.senkouA),
          KIJUN_SHIFT,
          SENKOU_A_COLOR,
          params,
        );
      }
      if (ichimokuSettings.senkouB) {
        drawPolyline(
          ctx,
          values.map((value) => value.senkouB),
          KIJUN_SHIFT,
          SENKOU_B_COLOR,
          params,
        );
      }
      if (ichimokuSettings.tenkan) {
        drawPolyline(
          ctx,
          values.map((value) => value.tenkan),
          0,
          TENKAN_COLOR,
          params,
        );
      }
      if (ichimokuSettings.kijun) {
        drawPolyline(
          ctx,
          values.map((value) => value.kijun),
          0,
          KIJUN_COLOR,
          params,
        );
      }
      if (ichimokuSettings.chikou) {
        drawPolyline(
          ctx,
          kLineDataList.map((bar) => bar.close),
          -KIJUN_SHIFT,
          CHIKOU_COLOR,
          params,
        );
      }

      ctx.restore();
      // Đã vẽ toàn bộ, không để KLineChart vẽ lại figure phía trên.
      return true;
    },
  });
}
