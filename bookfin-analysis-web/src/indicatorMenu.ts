import { flushIndicatorLayout } from "./indicatorLayoutStore";
/**
 * Chèn chỉ báo BookFin vào modal "Indicator" của KLineChart Pro.
 *
 * Pro 0.1.1 hardcode danh sách chỉ báo, nên hai chỉ báo custom (ICHIMOKU,
 * DIVERGENCE) được bổ sung vào DOM của modal. Mỗi dòng có icon bánh răng mở modal
 * cài đặt riêng, giữ modal Indicator gọn như UI gốc.
 */

import { persistChartSettings } from "./chartSettingsStore";
import { hasMainIndicator, requestMainToggle, setMainIndicator, subIndicators } from "./indicatorState";
import { customIndicator, openIndicatorSettings } from "./indicatorSettingsModal";

const LIST_SELECTOR = "ul.klinecharts-pro-indicator-modal-list";
const MARK = "data-bookfin-injected";

/** Thứ tự danh sách chỉ báo pane phụ mà Pro hardcode trong modal. */
const PRO_SUB_INDICATORS = [
  "MA",
  "EMA",
  "VOL",
  "MACD",
  "BOLL",
  "KDJ",
  "RSI",
  "BIAS",
  "BRAR",
  "CCI",
  "DMI",
  "CR",
  "PSY",
  "DMA",
  "TRIX",
  "OBV",
  "VR",
  "WR",
  "MTM",
  "EMV",
  "SAR",
  "SMA",
  "ROC",
  "PVT",
  "BBI",
  "AO",
];

const MAIN_ITEMS: { name: "ICHIMOKU" | "DIVERGENCE"; label: string }[] = [
  { name: "ICHIMOKU", label: "Ichimoku" },
  { name: "DIVERGENCE", label: "Divergence" },
];

const GEAR_ICON =
  '<svg viewBox="0 0 1024 1024" aria-hidden="true"><path d="M512 336a176 176 0 1 0 0 352 176 176 0 0 0 0-352zm0 272a96 96 0 1 1 0-192 96 96 0 0 1 0 192z"></path><path d="M906.7 458.7h-57.6a344 344 0 0 0-19.2-64l40.6-40.6a42.7 42.7 0 0 0 0-60.4l-40.4-40.4a42.7 42.7 0 0 0-60.4 0l-40.6 40.6a344 344 0 0 0-64-19.2v-57.6a42.7 42.7 0 0 0-42.7-42.7h-57.2a42.7 42.7 0 0 0-42.7 42.7v57.6a344 344 0 0 0-64 19.2l-40.6-40.6a42.7 42.7 0 0 0-60.4 0l-40.4 40.4a42.7 42.7 0 0 0 0 60.4l40.6 40.6a344 344 0 0 0-19.2 64h-57.6A42.7 42.7 0 0 0 259 501.3v57.2a42.7 42.7 0 0 0 42.7 42.7h57.6a344 344 0 0 0 19.2 64l-40.6 40.6a42.7 42.7 0 0 0 0 60.4l40.4 40.4a42.7 42.7 0 0 0 60.4 0l40.6-40.6a344 344 0 0 0 64 19.2v57.6a42.7 42.7 0 0 0 42.7 42.7h57.2a42.7 42.7 0 0 0 42.7-42.7v-57.6a344 344 0 0 0 64-19.2l40.6 40.6a42.7 42.7 0 0 0 60.4 0l40.4-40.4a42.7 42.7 0 0 0 0-60.4l-40.6-40.6a344 344 0 0 0 19.2-64h57.6a42.7 42.7 0 0 0 42.7-42.7v-57.2a42.7 42.7 0 0 0-42.7-42.7zM512 656a144 144 0 1 1 0-288 144 144 0 0 1 0 288z"></path></svg>';

function label(text: string, enabled: boolean, onChange: (value: boolean) => void): HTMLLabelElement {
  const wrapper = document.createElement("label");
  wrapper.className = "bookfin-toggle";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = enabled;
  input.addEventListener("change", () => onChange(input.checked));
  const span = document.createElement("span");
  span.textContent = text;
  wrapper.append(input, span);
  return wrapper;
}

function settingsButton(name: string): HTMLSpanElement {
  const button = document.createElement("span");
  button.className = "bookfin-gear";
  button.title = "Cài đặt chỉ báo";
  button.setAttribute("role", "button");
  button.innerHTML = GEAR_ICON;
  button.addEventListener("click", (event) => {
    // Không để click lan ra dòng chỉ báo (sẽ bật/tắt chỉ báo).
    event.stopPropagation();
    event.preventDefault();
    closeProModal();
    openIndicatorSettings(name);
  });
  return button;
}

/** Đóng modal Indicator của Pro trước khi mở modal cài đặt của BookFin. */
function closeProModal(): void {
  const list = document.querySelector(`.klinecharts-pro-indicator-modal-list`);
  const modal = list?.closest(".klinecharts-pro-modal");
  const closeIcon = modal?.querySelector(".title-container .close-icon");
  closeIcon?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

function inject(list: HTMLUListElement): void {
  const subTitle = list.querySelectorAll("li.title")[1];
  if (!subTitle) return;
  if (list.querySelector(`li[${MARK}]`)) return;

  const rows = MAIN_ITEMS.map((item) => {
    const row = document.createElement("li");
    row.className = "row";
    row.setAttribute(MARK, "1");
    row.append(
      label(item.label, hasMainIndicator(item.name), (value) => {
        flushIndicatorLayout();
        setMainIndicator(item.name, value);
        persistChartSettings();
        requestMainToggle(item.name, value);
      }),
    );
    if (customIndicator(item.name)) row.append(settingsButton(item.name));
    return row;
  });

  subTitle.before(...rows);
}

/**
 * Pro tự bật/tắt chỉ báo pane phụ trong modal; state của BookFin phải theo dõi
 * cùng thao tác để giữ nguyên danh sách khi chart phải dựng lại.
 */
function trackSubIndicators(list: HTMLUListElement): void {
  list.addEventListener(
    "click",
    (event) => {
      const target = event.target as HTMLElement | null;
      const clicked = target?.closest("li.row");
      if (!(clicked instanceof HTMLLIElement) || clicked.hasAttribute(MARK)) return;

      const subTitle = list.querySelectorAll("li.title")[1];
      if (!subTitle) return;
      const subRows = [...list.querySelectorAll("li.row")].filter(
        (item) => (item.compareDocumentPosition(subTitle) & Node.DOCUMENT_POSITION_PRECEDING) !== 0,
      );
      const name = PRO_SUB_INDICATORS[subRows.indexOf(clicked)];
      if (!name) return;

      const existing = subIndicators.indexOf(name);
      if (existing >= 0) subIndicators.splice(existing, 1);
      else subIndicators.push(name);
      persistChartSettings();
    },
    { capture: true },
  );
}

/** Theo dõi modal Indicator và bổ sung mục của BookFin khi modal mở. */
export function installIndicatorMenu(): void {
  const decorated = new WeakSet<HTMLUListElement>();

  const observer = new MutationObserver(() => {
    const list = document.querySelector<HTMLUListElement>(LIST_SELECTOR);
    if (!list) return;
    inject(list);
    if (!decorated.has(list)) {
      decorated.add(list);
      trackSubIndicators(list);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
