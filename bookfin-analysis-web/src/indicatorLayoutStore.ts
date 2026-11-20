/**
 * Đồng bộ trạng thái indicator thật của chart với localStorage.
 *
 * Pro không lưu bất kỳ lựa chọn indicator nào: bật/tắt, ẩn/hiện hay đổi tham số
 * đều mất khi reload. Module này đọc trạng thái thật từ chart sau mỗi thay đổi,
 * lưu lại, rồi áp lại sau khi chart khởi tạo lại.
 */

import type { Chart, Indicator, IndicatorCreate } from "klinecharts";
import {
  indicatorOverrides,
  persistChartSettings,
  replaceIndicatorOverrides,
  type IndicatorOverride,
} from "./chartSettingsStore";
import { mainIndicatorNames, setMainIndicator, subIndicators } from "./indicatorState";

let chart: Chart | null = null;
let saveTimer: number | null = null;
let applying = false;
// Chỉ lưu sau khi đã áp override cho chart vừa tạo; nếu không, trạng thái mặc
// định do Pro tạo ra sẽ ghi đè lên lựa chọn đã lưu của người dùng.
let ready = false;

function panes(): Map<string, Map<string, Indicator>> | null {
  const value = chart?.getIndicatorByPaneId();
  return value instanceof Map ? (value as Map<string, Map<string, Indicator>>) : null;
}

function saveLayout(): void {
  if (applying) return;
  const map = panes();
  if (!map) return;

  // Chỉ giữ override của indicator còn trong lựa chọn người dùng. Tránh
  // override cũ (ví dụ VOL/DMI đã xoá) làm ẩn hoặc đổi tham số khi thêm lại.
  const allowed = new Set([...mainIndicatorNames(), ...subIndicators]);
  const overrides: Record<string, IndicatorOverride> = {};
  for (const [name, override] of Object.entries(indicatorOverrides())) {
    if (allowed.has(name)) overrides[name] = override;
  }

  for (const [, indicators] of map) {
    for (const [name, indicator] of indicators) {
      if (!allowed.has(name)) continue;
      const params = Array.isArray(indicator.calcParams)
        ? indicator.calcParams.filter(
            (param): param is number | string =>
              typeof param === "number" || typeof param === "string",
          )
        : undefined;
      overrides[name] = {
        // A preceding Pro override is async. Its requested visibility is
        // authoritative until the library finishes updating its instance.
        visible: overrides[name]?.visible ?? indicator.visible,
        ...((overrides[name]?.calcParams ?? params)?.length
          ? { calcParams: overrides[name]?.calcParams ?? params }
          : {}),
      };
    }
  }

  // `mainIndicators`/`subIndicators` are maintained by the menu and are the
  // durable user intent. Reading the asynchronously-built Pro map here can
  // capture a partial layout during startup/realtime and re-enable defaults on
  // the next reload. This function only augments that intent with per-instance
  // visibility and parameters.
  replaceIndicatorOverrides(overrides);
  persistChartSettings();
}

/** Ghi trạng thái indicator sau một nhịp để gộp các thay đổi liên tiếp. */
function scheduleLayoutSave(delay = 250): void {
  if (applying || !ready) return;
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    saveTimer = null;
    saveLayout();
  }, delay);
}

/** Áp lại override đã lưu (ẩn/hiện, tham số) cho các indicator vừa được tạo. */
export function applyIndicatorOverrides(): void {
  const map = panes();
  if (!map) return;
  const saved = indicatorOverrides();

  applying = true;
  try {
    for (const [paneId, indicators] of map) {
      for (const name of [...indicators.keys()]) {
        const override = saved[name];
        if (!override) continue;
        const payload: IndicatorCreate = { name };
        if (override.visible !== undefined) payload.visible = override.visible;
        if (override.calcParams && override.calcParams.length > 0) {
          payload.calcParams = override.calcParams;
        }
        chart?.overrideIndicator(payload, paneId);
      }
    }
  } finally {
    applying = false;
    ready = true;
  }
  // Ghi lại trạng thái sau khi áp thành công để bản lưu khớp với chart.
  scheduleLayoutSave(400);
}

/**
 * Gắn persistence vào chart vừa tạo.
 *
 * Bọc các thao tác tạo/xoá/override indicator mà Pro gọi để biết khi nào cần lưu.
 */
export function attachIndicatorPersistence(instance: Chart): void {
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = null;
  chart = instance;
  ready = false;

  const originalCreate = instance.createIndicator.bind(instance);
  instance.createIndicator = ((...args: Parameters<Chart["createIndicator"]>) => {
    const created = originalCreate(...args);
    const [request, , pane] = args;
    const name = typeof request === "string" ? request : request?.name;
    if (!applying && created && typeof name === "string") {
      if (pane?.id === "candle_pane") setMainIndicator(name, true);
      else if (!subIndicators.includes(name)) subIndicators.push(name);
      persistChartSettings();
    }
    scheduleLayoutSave();
    return created;
  }) as Chart["createIndicator"];

  const originalRemove = instance.removeIndicator.bind(instance);
  instance.removeIndicator = ((...args: Parameters<Chart["removeIndicator"]>) => {
    const [paneId, name] = args;
    originalRemove(...args);
    if (!applying && typeof name === "string") {
      if (paneId === "candle_pane") setMainIndicator(name, false);
      else {
        const index = subIndicators.indexOf(name);
        if (index >= 0) subIndicators.splice(index, 1);
      }
      persistChartSettings();
    }
    scheduleLayoutSave();
  }) as Chart["removeIndicator"];

  const originalOverride = instance.overrideIndicator.bind(instance);
  instance.overrideIndicator = ((...args: Parameters<Chart["overrideIndicator"]>) => {
    const [request] = args;
    if (!applying && request && typeof request.name === "string") {
      const current = indicatorOverrides()[request.name] ?? {};
      const next: IndicatorOverride = { ...current };
      if (typeof request.visible === "boolean") next.visible = request.visible;
      if (Array.isArray(request.calcParams)) {
        next.calcParams = request.calcParams.filter(
          (param): param is number | string => typeof param === "number" || typeof param === "string",
        );
      }
      replaceIndicatorOverrides({ ...indicatorOverrides(), [request.name]: next });
      // Persist the user's command before an async library update or pagehide.
      persistChartSettings();
    }
    originalOverride(...args);
    scheduleLayoutSave();
  }) as Chart["overrideIndicator"];
}

/** Capture pending chart mutations synchronously before reload or remount. */
export function flushIndicatorLayout(): void {
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = null;
  // User commands have already been persisted. Reading the async Pro instance
  // during pagehide can resurrect the old visibility or a removed indicator.
}
