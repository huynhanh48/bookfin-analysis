import { utils, type OverlayFigure, type OverlayTemplate } from "klinecharts";

/**
 * Thước đo hai điểm A→B, port từ `@klinecharts/extension` (Apache-2.0).
 *
 * Hiển thị vùng chữ nhật giữa hai điểm cùng các số đo do `extendData` cung cấp:
 * % thay đổi, chênh lệch giá và khoảng thời gian.
 */

const COLOR = "#1677FF";

/** Số đo hiển thị trong hộp thước đo, tính từ hai điểm của overlay. */
export function measureTexts(points: Array<{ timestamp?: number; value?: number }>): string[] {
  const [start, end] = points;
  if (!start || !end) return [];
  const startValue = Number(start.value);
  const endValue = Number(end.value);
  if (!Number.isFinite(startValue) || !Number.isFinite(endValue) || startValue === 0) return [];

  const change = endValue - startValue;
  const percent = (change / startValue) * 100;
  const texts = [
    `${percent >= 0 ? "+" : ""}${percent.toFixed(2)}%`,
    `${change >= 0 ? "+" : ""}${change.toLocaleString("en-US", { maximumFractionDigits: 2 })}`,
  ];
  if (typeof start.timestamp === "number" && typeof end.timestamp === "number") {
    const days = Math.round(Math.abs(end.timestamp - start.timestamp) / 86_400_000);
    texts.push(`${days} day`);
  }
  return texts;
}

export const measure: OverlayTemplate = {
  name: "measure",
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  styles: {
    backgroundColor: "rgba(22, 119, 255, 0.25)",
    tipBackgroundColor: COLOR,
    lineColor: COLOR,
  },
  createPointFigures: ({ coordinates, overlay, bounding }) => {
    if (coordinates.length < 2) return [];

    const leftToRight = coordinates[0].x < coordinates[1].x;
    const topToBottom = coordinates[0].y < coordinates[1].y;
    const center = {
      x: Math.round((coordinates[0].x + coordinates[1].x) / 2),
      y: Math.round((coordinates[0].y + coordinates[1].y) / 2),
    };
    const styles = overlay.styles ?? {};
    const backgroundColor = (styles.backgroundColor as string) ?? "rgba(22, 119, 255, 0.25)";
    const tipBackgroundColor = (styles.tipBackgroundColor as string) ?? COLOR;
    const lineColor = (styles.lineColor as string) ?? COLOR;
    const texts = measureTexts(overlay.points);

    const figures: OverlayFigure[] = [
      {
        type: "polygon",
        attrs: {
          coordinates: [
            coordinates[0],
            { x: coordinates[1].x, y: coordinates[0].y },
            coordinates[1],
            { x: coordinates[0].x, y: coordinates[1].y },
          ],
        },
        styles: { color: backgroundColor },
      },
      {
        type: "line",
        attrs: {
          coordinates: [
            { x: coordinates[0].x, y: center.y },
            { x: coordinates[1].x, y: center.y },
          ],
        },
        styles: { color: lineColor },
      },
      {
        type: "line",
        attrs: {
          coordinates: [
            { x: center.x, y: coordinates[0].y },
            { x: center.x, y: coordinates[1].y },
          ],
        },
        styles: { color: lineColor },
      },
    ];

    // Mũi tên chỉ hướng đo.
    figures.push({
      type: "line",
      attrs: {
        coordinates: leftToRight
          ? [
              { x: coordinates[1].x - 6, y: center.y - 4 },
              { x: coordinates[1].x, y: center.y },
              { x: coordinates[1].x - 6, y: center.y + 4 },
            ]
          : [
              { x: coordinates[1].x + 6, y: center.y - 4 },
              { x: coordinates[1].x, y: center.y },
              { x: coordinates[1].x + 6, y: center.y + 4 },
            ],
      },
      styles: { color: lineColor },
    });
    figures.push({
      type: "line",
      attrs: {
        coordinates: topToBottom
          ? [
              { x: center.x - 4, y: coordinates[1].y - 6 },
              { x: center.x, y: coordinates[1].y },
              { x: center.x + 4, y: coordinates[1].y - 6 },
            ]
          : [
              { x: center.x - 4, y: coordinates[1].y + 6 },
              { x: center.x, y: coordinates[1].y },
              { x: center.x + 4, y: coordinates[1].y + 6 },
            ],
      },
      styles: { color: lineColor },
    });

    if (texts.length === 0) return figures;

    const tipGap = 8;
    const textGap = 4;
    const horizontalPadding = 12;
    const verticalPadding = 8;
    const lineHeight = 12;
    const height = texts.length * lineHeight + (texts.length - 1) * textGap + verticalPadding * 2;
    let width = 0;
    for (const text of texts) width = Math.max(utils.calcTextWidth(text), width);
    width += horizontalPadding * 2;

    let top: number;
    if (topToBottom) {
      top = coordinates[1].y + tipGap + height > bounding.height
        ? bounding.height - height
        : coordinates[1].y + tipGap;
    } else {
      top = coordinates[1].y - tipGap - height < 0 ? 0 : coordinates[1].y - tipGap - height;
    }

    figures.push({
      type: "rect",
      attrs: { x: center.x - width / 2, y: top, width, height },
      styles: { borderRadius: 4, color: tipBackgroundColor },
    });

    let textY = top + verticalPadding;
    for (const text of texts) {
      figures.push({
        type: "text",
        attrs: { x: center.x, y: textY, text, align: "center" },
        styles: {
          paddingLeft: 0,
          paddingTop: 0,
          paddingRight: 0,
          paddingBottom: 0,
          backgroundColor: "none",
          color: "#ffffff",
        },
      });
      textY += lineHeight + textGap;
    }
    return figures;
  },
};
