import { registerOverlay, type OverlayFigure, type OverlayTemplate } from "klinecharts";

/**
 * Overlay đếm sóng Elliott và mô hình giá, port từ `@klinecharts/extension`
 * (Apache-2.0) sang API của KLineChart 9.
 *
 * KLineChart Pro 0.1.1 đã có sẵn nút trên thanh vẽ (`threeWaves`, `fiveWaves`,
 * `eightWaves`, `anyWaves`, `abcd`, `xabcd`) nhưng bản 9.x chưa đăng ký template
 * nên bấm vào không vẽ được. Đăng ký ở đây là các nút đó hoạt động ngay.
 */

interface WaveOptions {
  name: string;
  totalStep: number;
  labels?: readonly string[];
}

function wave({ name, totalStep, labels }: WaveOptions): OverlayTemplate {
  return {
    name,
    totalStep,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true,
    createPointFigures: ({ coordinates }) =>
      coordinates.map((coordinate, index): OverlayFigure => {
        const label = labels?.[index] ?? String(index);
        const corrective = label === "A" || label === "B" || label === "C";
        return {
          type: "text",
          attrs: {
            ...coordinate,
            y: coordinate.y - 4,
            text: `(${label})`,
            align: "center",
            baseline: "bottom",
          },
          styles: corrective ? { color: "#c2410c", weight: "bold" } : undefined,
        };
      }),
  };
}

const TAGS = ["A", "B", "C", "D"];

const abcd: OverlayTemplate = {
  name: "abcd",
  totalStep: 5,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, index) => ({
      ...coordinate,
      baseline: "bottom" as const,
      text: `(${TAGS[index] ?? index})`,
    }));
    const figures: OverlayFigure[] = [
      { type: "line", attrs: { coordinates } },
      { type: "text", ignoreEvent: true, attrs: texts },
    ];
    if (coordinates.length > 2) {
      const diagonals = [{ coordinates: [coordinates[0], coordinates[2]] }];
      if (coordinates.length > 3) diagonals.push({ coordinates: [coordinates[1], coordinates[3]] });
      figures.splice(1, 0, { type: "line", attrs: diagonals, styles: { style: "dashed" } });
    }
    return figures;
  },
};

const XTAGS = ["X", "A", "B", "C", "D"];

const xabcd: OverlayTemplate = {
  name: "xabcd",
  totalStep: 6,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  styles: { polygon: { color: "rgba(22, 119, 255, 0.15)" } },
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, index) => ({
      ...coordinate,
      baseline: "bottom" as const,
      text: `(${XTAGS[index] ?? index})`,
    }));
    const dashed: { coordinates: typeof coordinates }[] = [];
    const polygons: { coordinates: typeof coordinates }[] = [];
    if (coordinates.length > 2) {
      dashed.push({ coordinates: [coordinates[0], coordinates[2]] });
      polygons.push({ coordinates: [coordinates[0], coordinates[1], coordinates[2]] });
      if (coordinates.length > 3) {
        dashed.push({ coordinates: [coordinates[1], coordinates[3]] });
        if (coordinates.length > 4) {
          dashed.push({ coordinates: [coordinates[2], coordinates[4]] });
          polygons.push({ coordinates: [coordinates[2], coordinates[3], coordinates[4]] });
        }
      }
    }
    return [
      { type: "line", attrs: { coordinates } },
      { type: "line", attrs: dashed, styles: { style: "dashed" } },
      { type: "polygon", ignoreEvent: true, attrs: polygons },
      { type: "text", ignoreEvent: true, attrs: texts },
    ];
  },
};

const TEMPLATES: OverlayTemplate[] = [
  wave({ name: "threeWaves", totalStep: 5, labels: ["0", "A", "B", "C"] }),
  wave({ name: "fiveWaves", totalStep: 7 }),
  wave({
    name: "eightWaves",
    totalStep: 10,
    labels: ["0", "1", "2", "3", "4", "5", "A", "B", "C"],
  }),
  wave({ name: "anyWaves", totalStep: Number.MAX_SAFE_INTEGER }),
  abcd,
  xabcd,
];

/** Đăng ký các overlay sóng/mô hình giá; gọi một lần trước khi tạo chart. */
export function registerWaveOverlays(): void {
  for (const template of TEMPLATES) registerOverlay(template);
}
