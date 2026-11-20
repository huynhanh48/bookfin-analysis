/**
 * Lưu và khôi phục overlay (sóng Elliott, mô hình giá…) vào localStorage.
 *
 * KLineChart Pro 0.1.1 không expose chart instance, nên `scripts/patch-klinecharts-pro.mjs`
 * bổ sung `getChart()` vào API của wrapper; nếu bản cài không có patch này thì mọi
 * thứ vẫn chạy bình thường, chỉ là nét vẽ không được lưu.
 *
 * Dữ liệu lưu theo từng mã + khung thời gian, dạng điểm (timestamp, giá) để khôi
 * phục lại đúng vị trí sau khi reload hoặc khi dữ liệu được tải lại.
 */

import type { Chart, Overlay } from "klinecharts";

/**
 * KLineChart không khai báo hai hàm này trong type công khai nhưng instance
 * overlay có: `isDrawing()` và `forceComplete()`. Cần chúng để không khôi phục
 * một overlay ở trạng thái "đang vẽ" (sẽ dính vào chuột).
 */
type DrawableOverlay = Overlay & {
  isDrawing?: () => boolean;
  forceComplete?: () => void;
};

const STORAGE_PREFIX = "bookfin:overlays:v1:";
const MAX_OVERLAYS = 40;

interface OverlayPoint {
  timestamp?: number;
  dataIndex?: number;
  value: number;
}

interface StoredOverlay {
  name: string;
  points: OverlayPoint[];
  visible?: boolean;
}

let chart: Chart | null = null;
let scopeProvider: () => string = () => "default";
let currentScope: string | null = null;
let tracking = new Set<string>();
let restoring = false;
let saveTimer: number | null = null;

function key(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`;
}

function readStored(scope: string): StoredOverlay[] {
  try {
    const raw = window.localStorage.getItem(key(scope));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isStoredOverlay).slice(0, MAX_OVERLAYS);
  } catch {
    return [];
  }
}

function isStoredOverlay(value: unknown): value is StoredOverlay {
  if (typeof value !== "object" || value === null) return false;
  const item = value as StoredOverlay;
  return (
    typeof item.name === "string" &&
    Array.isArray(item.points) &&
    item.points.every(
      (point) =>
        ((typeof point?.timestamp === "number" && Number.isFinite(point.timestamp)) ||
          (typeof point?.dataIndex === "number" && Number.isFinite(point.dataIndex))) &&
        typeof point?.value === "number" &&
        Number.isFinite(point.value),
    )
  );
}

function snapshot(target: Overlay): StoredOverlay | null {
  // Future-space anchors have dataIndex but no timestamp in KLineCharts v9.
  // Never filter individual anchors: that silently changes the drawing geometry.
  const points: OverlayPoint[] = target.points.map((point) => ({
    ...(typeof point.timestamp === "number" && Number.isFinite(point.timestamp)
      ? { timestamp: point.timestamp }
      : { dataIndex: point.dataIndex }),
    value: point.value as number,
  }));
  if (!isStoredOverlay({ name: target.name, points })) return null;
  if (points.length === 0) return null;
  return { name: target.name, points, visible: target.visible };
}

function saveNow(): void {
  if (!chart || !currentScope) return;
  const overlays: StoredOverlay[] = [];
  for (const id of tracking) {
    const target = chart.getOverlayById(id);
    if (!target) continue;
    // Chỉ lưu overlay đã hoàn tất. KLineChart đánh dấu hoàn tất bằng
    // `currentStep === -1`, hoặc khi số điểm đã đủ `totalStep - 1` (overlay vẽ
    // xong nhưng chưa bấm thêm lần nữa). Overlay đang vẽ dở bị bỏ qua để khi
    // khôi phục không bám chuột.
    const drawable = target as DrawableOverlay;
    const complete =
      drawable.currentStep === -1 ||
      target.points.length >= (target.totalStep ?? Number.POSITIVE_INFINITY) - 1;
    if (!complete) continue;
    const data = snapshot(target);
    if (data) overlays.push(data);
  }
  try {
    window.localStorage.setItem(key(currentScope), JSON.stringify(overlays.slice(0, MAX_OVERLAYS)));
  } catch {
    // Bỏ qua khi trình duyệt chặn localStorage.
  }
}

/** Ghi ngay lập tức, dùng khi đóng trang. */
export function flushOverlaySave(): void {
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = null;
  saveNow();
}

/** Lưu overlay hiện tại sau một khoảng ngắn để gộp thao tác kéo/thả. */
export function scheduleOverlaySave(delay = 300): void {
  if (restoring) return;
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    saveTimer = null;
    saveNow();
  }, delay);
}

/**
 * Ép overlay về trạng thái hoàn tất.
 *
 * `createOverlay` với danh sách điểm ngắn hơn `totalStep - 1` sẽ đặt overlay vào
 * chế độ đang vẽ; với các overlay không giới hạn điểm (Any Waves) thì luôn như
 * vậy, nên phải đóng lại thủ công.
 */
function completeOverlay(created: ReturnType<Chart["createOverlay"]>): void {
  if (!chart) return;
  const ids = typeof created === "string" ? [created] : Array.isArray(created) ? created : [];
  for (const id of ids) {
    if (typeof id !== "string") continue;
    const target = chart.getOverlayById(id) as DrawableOverlay | null;
    if (target?.isDrawing?.() === true) target.forceComplete?.();
  }
}

function track(created: ReturnType<Chart["createOverlay"]>): void {
  if (typeof created === "string") tracking.add(created);
  else if (Array.isArray(created)) {
    for (const id of created) if (typeof id === "string") tracking.add(id);
  }
}

/** Khôi phục overlay đã lưu của phạm vi hiện tại (gọi sau khi dữ liệu đã nạp). */
export function restoreOverlays(): void {
  if (!chart || !currentScope) return;
  const stored = readStored(currentScope);
  if (stored.length === 0) return;

  restoring = true;
  try {
    for (const item of stored) {
      const created = chart.createOverlay({
        name: item.name,
        points: item.points,
        visible: item.visible ?? true,
      });
      track(created);
      completeOverlay(created);
    }
  } catch {
    // Overlay lạ (template chưa đăng ký): bỏ qua phần còn lại.
  } finally {
    restoring = false;
  }
  // Vẽ lại để overlay vừa khôi phục hiện đúng ngay, không chờ thao tác chuột.
  chart.resize();
}

/**
 * Đồng bộ overlay với mã/khung đang xem.
 *
 * Gọi sau mỗi lần dữ liệu được tải: nếu phạm vi đổi thì xoá nét vẽ của phạm vi cũ
 * và khôi phục nét vẽ của phạm vi mới, tránh lẫn giữa các mã.
 */
export function syncOverlayScope(): void {
  if (!chart) return;
  const scope = scopeProvider();
  if (scope === currentScope) {
    // Cùng mã/khung: chỉ khôi phục khi chart mới chưa có nét vẽ nào. Nếu không,
    // mỗi lần cuộn nạp thêm nến sẽ tạo overlay trùng.
    if (tracking.size === 0) restoreOverlays();
    return;
  }
  flushOverlaySave();
  chart.removeOverlay();
  tracking = new Set();
  currentScope = scope;
  restoreOverlays();
}

/** Xoá toàn bộ nét vẽ hiện tại và dữ liệu đã lưu của phạm vi đang xem. */
export function clearOverlays(): void {
  if (!chart) return;
  chart.removeOverlay();
  tracking = new Set();
  if (!currentScope) return;
  try {
    window.localStorage.removeItem(key(currentScope));
  } catch {
    // Bỏ qua khi trình duyệt chặn localStorage.
  }
}

/**
 * Gắn persistence vào chart vừa tạo.
 *
 * @param instance KLineChart instance thật lấy qua patch của Pro.
 * @param resolveScope Hàm trả về khoá phạm vi, ví dụ `SSI:1D`.
 */
export function attachOverlayPersistence(
  instance: Chart,
  resolveScope: () => string,
  root?: HTMLElement | null,
): void {
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = null;
  chart = instance;
  scopeProvider = resolveScope;
  currentScope = null;
  tracking = new Set();

  const originalApply = instance.applyNewData.bind(instance);
  instance.applyNewData = (...args: Parameters<Chart["applyNewData"]>) => {
    if (chart === instance && currentScope && scopeProvider() !== currentScope) {
      flushOverlaySave();
      instance.removeOverlay();
      currentScope = null;
    }
    return originalApply(...args);
  };

  const originalCreate = instance.createOverlay.bind(instance);
  instance.createOverlay = ((...args: Parameters<Chart["createOverlay"]>) => {
    const created = originalCreate(...args);
    track(created);
    scheduleOverlaySave();
    return created;
  }) as Chart["createOverlay"];

  // Pro's drawing toolbar hides every overlay through overrideOverlay({visible}).
  // Capture that command as well as edits to a single overlay before reload.
  const originalOverride = instance.overrideOverlay.bind(instance);
  instance.overrideOverlay = ((...args: Parameters<Chart["overrideOverlay"]>) => {
    originalOverride(...args);
    if (args[0] && "visible" in args[0] && typeof args[0].visible === "boolean") {
      flushOverlaySave();
    } else {
      scheduleOverlaySave();
    }
  }) as Chart["overrideOverlay"];

  // Kéo/thả điểm không đi qua API công khai, nên lưu lại sau mỗi thao tác chuột.
  root?.addEventListener("mouseup", () => scheduleOverlaySave(400));
  root?.addEventListener("touchend", () => scheduleOverlaySave(400));

  const originalRemove = instance.removeOverlay.bind(instance);
  instance.removeOverlay = ((...args: Parameters<Chart["removeOverlay"]>) => {
    originalRemove(...args);
    if (args.length > 0) scheduleOverlaySave();
    else tracking = new Set();
  }) as Chart["removeOverlay"];
}
