/**
 * Bổ sung công cụ vẽ còn thiếu vào thanh vẽ của KLineChart Pro.
 *
 * Pro 0.1.1 không có nút cho thước đo (ruler) và không có UI cấu hình mức
 * Fibonacci, nên hai mục được chèn vào cuối thanh vẽ:
 *
 * - Thước đo: tạo overlay `measure` (đo % giá và số ngày giữa hai điểm).
 * - Fibonacci: mở modal bật/tắt từng mức của Fibonacci retracement.
 */

import { persistChartSettings } from "./chartSettingsStore";
import {
  DEFAULT_FIB_LEVELS,
  fibLevels,
  setFibLevels,
  type FibLevel,
} from "./overlays/fibonacci";

type OverlayCreator = (options: { name: string; extendData?: unknown }) => unknown;

let chartOverlayCreator: OverlayCreator | null = null;

/** Cho phép module này tạo overlay trên chart đang hiển thị. */
export function bindDrawingTools(createOverlay: OverlayCreator | null): void {
  chartOverlayCreator = createOverlay;
}

const RULER_ICON = `
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6">
  <rect x="2" y="8" width="20" height="8" rx="1.5"></rect>
  <path d="M6 8v3M10 8v4M14 8v3M18 8v4"></path>
</svg>`;

const FIB_ICON = `
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6">
  <path d="M3 4h18M3 9.2h18M3 14.4h18M3 19.6h18"></path>
  <circle cx="4" cy="4" r="1.6"></circle>
  <circle cx="20" cy="19.6" r="1.6"></circle>
</svg>`;

const MARK = "data-bookfin-tool";

function createTool(icon: string, title: string, onClick: () => void): HTMLDivElement {
  const item = document.createElement("div");
  item.className = "item bookfin-drawing-tool";
  item.setAttribute(MARK, "1");
  item.title = title;
  item.setAttribute("role", "button");
  item.tabIndex = 0;
  item.innerHTML = `<span class="bookfin-drawing-icon">${icon}</span>`;
  item.addEventListener("click", (event) => {
    event.stopPropagation();
    onClick();
  });
  return item;
}

function startMeasure(): void {
  if (!chartOverlayCreator) return;
  chartOverlayCreator({
    name: "measure",
    extendData: undefined,
  });
}

let dialog: HTMLElement | null = null;

function closeDialog(): void {
  dialog?.remove();
  dialog = null;
}

function applyLevels(levels: FibLevel[]): void {
  setFibLevels(levels);
  persistChartSettings();
  // Vẽ lại để overlay Fibonacci đang hiển thị dùng mức mới.
  window.dispatchEvent(new CustomEvent("bookfin:fib-levels-changed"));
}

/** Mở modal bật/tắt các mức Fibonacci. */
export function openFibonacciSettings(): void {
  closeDialog();
  const current = fibLevels();
  const known = new Set(current.map((level) => level.ratio));
  for (const level of DEFAULT_FIB_LEVELS) {
    if (!known.has(level.ratio)) current.push({ ...level, enabled: false });
  }
  current.sort((left, right) => right.ratio - left.ratio);

  const overlay = document.createElement("div");
  overlay.className = "klinecharts-pro-modal bookfin-settings-modal";
  overlay.innerHTML = `
    <div class="inner" style="width: 320px;">
      <div class="title-container">
        <span>Fibonacci retracement</span>
        <svg class="close-icon" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" fill="none"></path></svg>
      </div>
      <div class="content-container bookfin-settings-content bookfin-fib-levels">
        ${current
          .map(
            (level) => `
          <label class="bookfin-toggle" data-ratio="${level.ratio}">
            <input type="checkbox" ${level.enabled ? "checked" : ""} />
            <span>${(level.ratio * 100).toFixed(1)}%</span>
          </label>`,
          )
          .join("")}
      </div>
      <div class="button-container">
        <button class="bookfin-settings-close" type="button">Đóng</button>
      </div>
    </div>
  `;

  for (const input of overlay.querySelectorAll<HTMLInputElement>(".bookfin-toggle input")) {
    const label = input.closest<HTMLElement>(".bookfin-toggle");
    const ratio = Number(label?.dataset.ratio);
    input.addEventListener("change", () => {
      const next = fibLevels().map((level) =>
        level.ratio === ratio ? { ...level, enabled: input.checked } : level,
      );
      for (const level of current) {
        if (!next.some((item) => item.ratio === level.ratio)) {
          next.push({ ...level, enabled: false });
        }
      }
      applyLevels(next);
    });
  }

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeDialog();
  });
  overlay.querySelector(".title-container .close-icon")?.addEventListener("click", closeDialog);
  overlay.querySelector(".bookfin-settings-close")?.addEventListener("click", closeDialog);
  document.body.append(overlay);
  dialog = overlay;
}

/** Chèn công cụ vào thanh vẽ của Pro (một lần duy nhất). */
export function installDrawingTools(): void {
  const decorate = (): void => {
    const bar = document.querySelector(".klinecharts-pro-drawing-bar");
    if (!bar || bar.querySelector(`[${MARK}]`)) return;
    const divider = document.createElement("span");
    divider.className = "split-line";
    divider.setAttribute(MARK, "1");
    bar.append(
      divider,
      createTool(RULER_ICON, "Thước đo (A→B)", startMeasure),
      createTool(FIB_ICON, "Cài đặt mức Fibonacci", openFibonacciSettings),
    );
  };

  const observer = new MutationObserver(decorate);
  observer.observe(document.body, { childList: true, subtree: true });
  decorate();
}
