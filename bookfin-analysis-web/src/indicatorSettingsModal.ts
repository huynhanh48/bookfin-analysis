/**
 * Cài đặt cho các chỉ báo custom của BookFin.
 *
 * Chỉ báo custom không có mục trong modal cài đặt mặc định của KLineChart Pro,
 * nên mỗi chỉ báo có một icon bánh răng trong modal Indicator; bấm vào sẽ mở
 * modal riêng chỉ chứa các tuỳ chọn của chỉ báo đó.
 */

import { persistChartSettings } from "./chartSettingsStore";
import {
  ichimokuSettings,
  requestRepaint,
  setShowHiddenDivergence,
  showHiddenDivergence,
} from "./indicatorState";

/** Một tuỳ chọn bật/tắt trong modal cài đặt. */
export interface SettingToggle {
  readonly id: string;
  readonly label: string;
  readonly get: () => boolean;
  readonly set: (value: boolean) => void;
}

/** Định nghĩa cài đặt của một chỉ báo custom. */
export interface CustomIndicatorDefinition {
  readonly name: string;
  readonly label: string;
  readonly toggles: readonly SettingToggle[];
}

function toggle(
  id: string,
  label: string,
  get: () => boolean,
  set: (value: boolean) => void,
): SettingToggle {
  return { id, label, get, set };
}

const DEFINITIONS: readonly CustomIndicatorDefinition[] = [
  {
    name: "ICHIMOKU",
    label: "Ichimoku",
    toggles: [
      toggle(
        "tenkan",
        "Tenkan-sen",
        () => ichimokuSettings.tenkan,
        (value) => {
          ichimokuSettings.tenkan = value;
          requestRepaint();
        },
      ),
      toggle(
        "kijun",
        "Kijun-sen",
        () => ichimokuSettings.kijun,
        (value) => {
          ichimokuSettings.kijun = value;
          requestRepaint();
        },
      ),
      toggle(
        "senkouA",
        "Senkou Span A",
        () => ichimokuSettings.senkouA,
        (value) => {
          ichimokuSettings.senkouA = value;
          requestRepaint();
        },
      ),
      toggle(
        "senkouB",
        "Senkou Span B",
        () => ichimokuSettings.senkouB,
        (value) => {
          ichimokuSettings.senkouB = value;
          requestRepaint();
        },
      ),
      toggle(
        "chikou",
        "Chikou Span",
        () => ichimokuSettings.chikou,
        (value) => {
          ichimokuSettings.chikou = value;
          requestRepaint();
        },
      ),
      toggle(
        "cloud",
        "Mây Kumo",
        () => ichimokuSettings.cloud,
        (value) => {
          ichimokuSettings.cloud = value;
          requestRepaint();
        },
      ),
    ],
  },
  {
    name: "DIVERGENCE",
    label: "Divergence",
    toggles: [
      toggle(
        "hidden",
        "Phân kỳ ẩn (nét đứt)",
        () => showHiddenDivergence,
        (value) => {
          setShowHiddenDivergence(value);
          requestRepaint();
        },
      ),
    ],
  },
];

/** Lấy định nghĩa cài đặt của một chỉ báo custom, nếu có. */
export function customIndicator(name: string): CustomIndicatorDefinition | undefined {
  return DEFINITIONS.find((definition) => definition.name === name);
}

const CLOSE_ICON =
  '<svg class="close-icon" viewBox="0 0 1024 1024"><path d="M934.184927 199.723787 622.457206 511.452531l311.727721 311.703161c14.334473 14.229073 23.069415 33.951253 23.069415 55.743582 0 43.430138-35.178197 78.660524-78.735226 78.660524-21.664416 0-41.361013-8.865925-55.642275-23.069415L511.149121 622.838388 199.420377 934.490384c-14.204513 14.20349-33.901111 23.069415-55.642275 23.069415-43.482327 0-78.737272-35.230386-78.737272-78.660524 0-21.792329 8.864902-41.513486 23.094998-55.743582l311.677579-311.703161L8.701777 199.723787c-14.230096-14.229073-23.094998-33.951253-23.094998-55.743582 0-43.430138 35.254945-78.660524 78.737272-78.660524 21.741164 0 41.437762 8.865925 55.642275 23.069415l311.728744 311.651866 311.728744-311.651866c14.204513-14.20349 33.901111-23.069415 55.642275-23.069415 43.557029 0 78.735226 35.230386 78.735226 78.660524 0 21.792329-8.734942 41.514509-23.069415 55.743582z"></path></svg>';

let openDialog: HTMLElement | null = null;

function closeSettings(): void {
  openDialog?.remove();
  openDialog = null;
}

/**
 * Mở modal cài đặt của một chỉ báo custom.
 *
 * Mọi thay đổi áp dụng ngay và vẽ lại chart; modal dùng lại style của
 * KLineChart Pro để đồng bộ giao diện.
 */
export function openIndicatorSettings(name: string): void {
  const definition = customIndicator(name);
  if (!definition) return;
  closeSettings();

  const overlay = document.createElement("div");
  overlay.className = "klinecharts-pro-modal bookfin-settings-modal";
  overlay.innerHTML = `
    <div class="inner" style="width: 340px;">
      <div class="title-container">
        <span>${definition.label}</span>
        ${CLOSE_ICON}
      </div>
      <div class="content-container bookfin-settings-content">
        ${definition.toggles
          .map(
            (item) => `
          <label class="bookfin-toggle" data-toggle="${item.id}">
            <input type="checkbox" ${item.get() ? "checked" : ""} />
            <span>${item.label}</span>
          </label>`,
          )
          .join("")}
      </div>
      <div class="button-container">
        <button class="bookfin-settings-close" type="button">Đóng</button>
      </div>
    </div>
  `;

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeSettings();
  });
  overlay.querySelector(".title-container .close-icon")?.addEventListener("click", closeSettings);
  overlay.querySelector(".bookfin-settings-close")?.addEventListener("click", closeSettings);

  for (const item of definition.toggles) {
    const input = overlay.querySelector<HTMLInputElement>(`[data-toggle="${item.id}"] input`);
    input?.addEventListener("change", () => {
      item.set(input.checked);
      persistChartSettings();
    });
  }

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape") closeSettings();
    },
    { once: true },
  );

  document.body.append(overlay);
  openDialog = overlay;
}
