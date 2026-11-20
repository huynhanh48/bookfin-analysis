/**
 * Lưu cài đặt chart vào localStorage để reload không phải thiết lập lại.
 *
 * Bao gồm: tuỳ chọn đường Ichimoku, phân kỳ ẩn, danh sách indicator đang bật và
 * các override (ẩn/hiện, tham số) của từng indicator.
 */

import { fibLevels, setFibLevels, type FibLevel } from "./overlays/fibonacci";
import {
  ichimokuSettings,
  replaceMainIndicators,
  replaceSubIndicators,
  setShowHiddenDivergence,
  showHiddenDivergence,
  subIndicators,
  mainIndicators,
} from "./indicatorState";

const STORAGE_KEY = "bookfin:chart-settings:v1";
const STORAGE_VERSION = 2;

/**
 * Default cũ của BookFin. Khi migrate lên version 2, chỉ bỏ các chỉ báo mặc
 * định cũ (MA/VOL) và giữ mọi chỉ báo người dùng đã thêm.
 */
const LEGACY_DEFAULT_MAIN = "MA";
const LEGACY_DEFAULT_SUB = "VOL";

/** Override của một indicator, áp lại sau mỗi lần chart khởi tạo. */
export interface IndicatorOverride {
  visible?: boolean;
  calcParams?: Array<number | string>;
}

interface StoredSettings {
  version?: number;
  ichimoku?: Partial<typeof ichimokuSettings>;
  showHiddenDivergence?: boolean;
  mainIndicators?: string[];
  subIndicators?: string[];
  overrides?: Record<string, IndicatorOverride>;
  fibLevels?: FibLevel[];
}

const overrides: Record<string, IndicatorOverride> = {};

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function isNameList(value: unknown): value is string[] {
  // An empty list is meaningful: it means the user disabled every indicator
  // in that group. Treating [] as invalid silently restored Pro defaults.
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function isOverride(value: unknown): value is IndicatorOverride {
  if (typeof value !== "object" || value === null) return false;
  const item = value as IndicatorOverride;
  if (item.visible !== undefined && !isBoolean(item.visible)) return false;
  if (item.calcParams !== undefined) {
    if (!Array.isArray(item.calcParams)) return false;
    if (!item.calcParams.every((param) => typeof param === "number" || typeof param === "string")) {
      return false;
    }
  }
  return true;
}

/** Override hiện tại theo tên indicator (được áp lại sau khi chart tạo indicator). */
export function indicatorOverrides(): Readonly<Record<string, IndicatorOverride>> {
  return overrides;
}

/**
 * Migrate settings cũ (chưa có version) sang default mới.
 *
 * Chỉ loại bỏ MA/VOL vốn là default cũ, không xoá các chỉ báo người dùng tự
 * thêm. Override tương ứng cũng bị xoá để lần thêm lại không bị ẩn nhầm.
 */
function migrateLegacyDefaults(stored: StoredSettings): void {
  if ((stored.version ?? 1) >= STORAGE_VERSION) return;
  if (isNameList(stored.mainIndicators)) {
    stored.mainIndicators = stored.mainIndicators.filter((name) => name !== LEGACY_DEFAULT_MAIN);
  }
  if (isNameList(stored.subIndicators)) {
    stored.subIndicators = stored.subIndicators.filter((name) => name !== LEGACY_DEFAULT_SUB);
  }
  if (stored.overrides && typeof stored.overrides === "object") {
    delete stored.overrides[LEGACY_DEFAULT_MAIN];
    delete stored.overrides[LEGACY_DEFAULT_SUB];
  }
  stored.version = STORAGE_VERSION;
}

/** Thay toàn bộ override (dùng khi đọc trạng thái thật từ chart). */
export function replaceIndicatorOverrides(value: Record<string, IndicatorOverride>): void {
  for (const key of Object.keys(overrides)) delete overrides[key];
  Object.assign(overrides, value);
}

/** Đọc cài đặt đã lưu và áp vào state; bỏ qua dữ liệu hỏng. */
export function hydrateChartSettings(): void {
  let stored: StoredSettings | null = null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    stored = JSON.parse(raw) as StoredSettings;
  } catch {
    return;
  }
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return;
  migrateLegacyDefaults(stored);

  for (const key of Object.keys(ichimokuSettings) as (keyof typeof ichimokuSettings)[]) {
    const value = stored.ichimoku?.[key];
    if (isBoolean(value)) ichimokuSettings[key] = value;
  }
  if (isBoolean(stored.showHiddenDivergence)) setShowHiddenDivergence(stored.showHiddenDivergence);
  if (isNameList(stored.mainIndicators)) replaceMainIndicators(stored.mainIndicators);
  if (isNameList(stored.subIndicators)) replaceSubIndicators(stored.subIndicators);

  if (Array.isArray(stored.fibLevels)) {
    const restored = stored.fibLevels.filter(
      (level): level is FibLevel =>
        typeof level?.ratio === "number" &&
        Number.isFinite(level.ratio) &&
        typeof level?.enabled === "boolean",
    );
    // Keep an empty list: all Fibonacci levels may be intentionally disabled.
    if (restored.length === stored.fibLevels.length) setFibLevels(restored);
  }

  if (stored.overrides && typeof stored.overrides === "object") {
    const restored: Record<string, IndicatorOverride> = {};
    for (const [name, value] of Object.entries(stored.overrides)) {
      if (isOverride(value)) restored[name] = value;
    }
    replaceIndicatorOverrides(restored);
  }
}

function write(): void {
  const payload: StoredSettings = {
    version: STORAGE_VERSION,
    ichimoku: { ...ichimokuSettings },
    showHiddenDivergence,
    mainIndicators: [...mainIndicators],
    subIndicators: [...subIndicators],
    overrides: { ...overrides },
    fibLevels: fibLevels(),
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Private mode or full storage: ignore, never block the UI.
  }
}

/** Ghi ngay lập tức, dùng khi đóng trang. */
export function flushChartSettings(): void {
  write();
}

/** Persist a small user setting immediately so reload sees the last command. */
export function persistChartSettings(): void {
  // This payload is small. Write user commands synchronously so a browser
  // reload cannot race a deferred timer or a realtime chart refresh.
  write();
}
