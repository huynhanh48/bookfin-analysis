import { IndicatorSeries, registerIndicator } from "klinecharts";
import { showHiddenDivergence } from "../indicatorState";
import { findSeries, type StructuralEvent } from "../seriesStore";

const BULLISH_COLOR = "#26a69a";
const BEARISH_COLOR = "#ef5350";
const LOCAL_WIDTH = 1.5;
const STRUCTURAL_WIDTH = 2.5;
// Nét chấm: đoạn vẽ ngắn, khoảng trống lớn để phân biệt với nét đứt của hidden.
const DOTTED: number[] = [2, 6];
const DASHED: number[] = [6, 4];

function colorOf(direction: string): string {
  return direction === "bullish" ? BULLISH_COLOR : BEARISH_COLOR;
}

/**
 * Phân kỳ giá–động lượng: local (nét liền/nét đứt) và cấu trúc (nét chấm).
 *
 * Toàn bộ phát hiện nằm ở BE. FE chỉ vẽ: local thuộc một cấu trúc hợp lệ được ẩn
 * đi để tránh trùng lặp, các local độc lập vẫn hiển thị bình thường.
 */
export function registerDivergence(): void {
  registerIndicator<Record<string, never>>({
    name: "DIVERGENCE",
    shortName: "DIVERGENCE",
    series: IndicatorSeries.Price,
    calcParams: [],
    precision: 2,
    shouldOhlc: false,
    figures: [],
    // Không tính toán gì trên FE: dữ liệu đã có trong payload của BE.
    calc: (dataList) => dataList.map(() => ({})),
    draw: ({ ctx, kLineDataList, xAxis, yAxis, bounding }) => {
      const payload = findSeries(kLineDataList);
      if (!payload) return false;

      const structuralEvents = payload.structuralEvents.filter(
        (event) => showHiddenDivergence || event.kind !== "hidden",
      );
      const mergedMembers = new Set(structuralEvents.flatMap((event) => event.memberIds));
      const localEvents = payload.divergenceEvents.filter(
        (event) =>
          !mergedMembers.has(event.id) && (showHiddenDivergence || event.kind !== "hidden"),
      );
      if (localEvents.length === 0 && structuralEvents.length === 0) return false;

      const indexByTimestamp = new Map<number, number>();
      kLineDataList.forEach((bar, index) => indexByTimestamp.set(bar.timestamp, index));

      ctx.save();
      // Toạ độ trục theo pane, còn bounding theo container: clip theo gốc pane.
      ctx.beginPath();
      ctx.rect(0, 0, bounding.width, bounding.height);
      ctx.clip();

      // Local: regular nét liền, hidden nét đứt, 1.5px.
      ctx.lineWidth = LOCAL_WIDTH;
      for (const event of localEvents) {
        const startIndex = indexByTimestamp.get(event.startTime);
        const endIndex = indexByTimestamp.get(event.endTime);
        if (startIndex === undefined || endIndex === undefined) continue;
        ctx.strokeStyle = colorOf(event.direction);
        ctx.setLineDash(event.kind === "hidden" ? DASHED : []);
        ctx.beginPath();
        ctx.moveTo(xAxis.convertToPixel(startIndex), yAxis.convertToPixel(event.startPrice));
        ctx.lineTo(xAxis.convertToPixel(endIndex), yAxis.convertToPixel(event.endPrice));
        ctx.stroke();
      }

      // Cấu trúc: nét chấm 2.5px + nhãn STR ở cuối đường.
      ctx.lineWidth = STRUCTURAL_WIDTH;
      ctx.font = "9px sans-serif";
      ctx.textAlign = "left";
      for (const event of structuralEvents) {
        drawStructural(ctx, event, indexByTimestamp, xAxis, yAxis);
      }

      ctx.restore();
      // Trả false để KLineChart vẫn vẽ nến phía dưới.
      return false;
    },
  });
}

function drawStructural(
  ctx: CanvasRenderingContext2D,
  event: StructuralEvent,
  indexByTimestamp: Map<number, number>,
  xAxis: { convertToPixel: (value: number) => number },
  yAxis: { convertToPixel: (value: number) => number },
): void {
  const startIndex = indexByTimestamp.get(event.startTime);
  const endIndex = indexByTimestamp.get(event.endTime);
  if (startIndex === undefined || endIndex === undefined) return;

  const color = colorOf(event.direction);
  const startX = xAxis.convertToPixel(startIndex);
  const startY = yAxis.convertToPixel(event.startPrice);
  const endX = xAxis.convertToPixel(endIndex);
  const endY = yAxis.convertToPixel(event.endPrice);

  ctx.strokeStyle = color;
  ctx.setLineDash(DOTTED);
  ctx.beginPath();
  ctx.moveTo(startX, startY);
  ctx.lineTo(endX, endY);
  ctx.stroke();
  ctx.setLineDash([]);

  // Nhãn nhỏ "STR" ở cuối đường; số pivot giúp phân biệt cấu trúc 3/4 pivot.
  ctx.fillStyle = color;
  ctx.fillText(event.pivotCount > 3 ? `STR·${event.pivotCount}` : "STR", endX + 4, endY - 4);
}
