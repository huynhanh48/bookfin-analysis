import type { Axis } from "klinecharts";

/** Một điểm của chỉ báo dùng cho vùng tô. */
export interface ZonePoint {
  index: number;
  value: number | null;
}

interface ZoneAxes {
  xAxis: Axis;
  yAxis: Axis;
}

/**
 * Tô vùng giữa đường chỉ báo và một ngưỡng cố định.
 *
 * Chỉ tô khi cả hai đầu của đoạn cùng vượt ngưỡng, nên khi đường quay lại dưới
 * (hoặc lên trên) ngưỡng thì vùng tô dừng đúng tại điểm cắt.
 *
 * @param direction 1 = tô khi giá trị lớn hơn ngưỡng, -1 = tô khi nhỏ hơn ngưỡng.
 */
export function fillZone(
  ctx: CanvasRenderingContext2D,
  points: ZonePoint[],
  boundary: number,
  axes: ZoneAxes,
  color: string,
  direction: 1 | -1,
): void {
  const boundaryY = axes.yAxis.convertToPixel(boundary);
  ctx.fillStyle = color;

  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    if (current.value == null || next.value == null) continue;
    const currentInside = direction > 0 ? current.value > boundary : current.value < boundary;
    const nextInside = direction > 0 ? next.value > boundary : next.value < boundary;
    if (!currentInside || !nextInside) continue;

    const currentX = axes.xAxis.convertToPixel(current.index);
    const nextX = axes.xAxis.convertToPixel(next.index);
    ctx.beginPath();
    ctx.moveTo(currentX, boundaryY);
    ctx.lineTo(currentX, axes.yAxis.convertToPixel(current.value));
    ctx.lineTo(nextX, axes.yAxis.convertToPixel(next.value));
    ctx.lineTo(nextX, boundaryY);
    ctx.closePath();
    ctx.fill();
  }
}

/** Kiểu vẽ đường ngưỡng. */
export interface BoundaryStyle {
  color?: string;
  dash?: number[];
  width?: number;
}

/** Vẽ đường ngưỡng cho chỉ báo dạng 0–100. */
export function drawBoundary(
  ctx: CanvasRenderingContext2D,
  boundary: number,
  axes: ZoneAxes,
  bounds: { left: number; right: number },
  style: BoundaryStyle = {},
): void {
  const y = axes.yAxis.convertToPixel(boundary);
  ctx.save();
  ctx.strokeStyle = style.color ?? "rgba(120, 130, 145, 0.55)";
  ctx.lineWidth = style.width ?? 1;
  ctx.setLineDash(style.dash ?? [4, 3]);
  ctx.beginPath();
  ctx.moveTo(bounds.left, y);
  ctx.lineTo(bounds.right, y);
  ctx.stroke();
  ctx.restore();
}
