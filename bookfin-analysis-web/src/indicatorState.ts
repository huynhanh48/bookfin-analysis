/**
 * Trạng thái indicator do BookFin quản lý.
 *
 * KLineChart Pro không nhận indicator custom trong modal mặc định, nên các lựa
 * chọn của BookFin được giữ ở đây (và lưu localStorage) rồi áp dụng qua cầu nối
 * `chartBridge` do `app.ts` cài đặt.
 */

/** Các đường Ichimoku có thể bật/tắt trong phần cài đặt của chart. */
export interface IchimokuSettings {
  tenkan: boolean;
  kijun: boolean;
  senkouA: boolean;
  senkouB: boolean;
  chikou: boolean;
  cloud: boolean;
}

export const ichimokuSettings: IchimokuSettings = {
  tenkan: true,
  kijun: true,
  senkouA: true,
  senkouB: true,
  chikou: true,
  cloud: true,
};

/** Hiện phân kỳ ẩn (hidden) - vẽ bằng nét đứt. */
export let showHiddenDivergence = true;

/** Bật/tắt hiển thị phân kỳ ẩn. */
export function setShowHiddenDivergence(value: boolean): void {
  showHiddenDivergence = value;
}

/**
 * Indicator trên pane giá, đúng thứ tự hiển thị.
 *
 * Đây là nguồn sự thật duy nhất: chart được dựng lại theo danh sách này nên
 * trạng thái bật/tắt luôn khớp với những gì người dùng thấy.
 *
 * Mặc định chỉ bật Ichimoku + Divergence; các chỉ báo khác do người dùng thêm.
 */
export const mainIndicators: string[] = ["ICHIMOKU", "DIVERGENCE"];

/** Có indicator này trên pane giá hay không. */
export function hasMainIndicator(name: string): boolean {
  return mainIndicators.includes(name);
}

/** Thêm/bớt một indicator trên pane giá. */
export function setMainIndicator(name: string, enabled: boolean): void {
  const index = mainIndicators.indexOf(name);
  if (enabled && index < 0) mainIndicators.push(name);
  if (!enabled && index >= 0) mainIndicators.splice(index, 1);
}

/** Thay toàn bộ danh sách indicator pane giá (dùng khi khôi phục từ chart). */
export function replaceMainIndicators(names: readonly string[]): void {
  mainIndicators.splice(0, mainIndicators.length, ...names);
}

/** Danh sách chỉ báo pane phụ đang bật, giữ nguyên khi chart dựng lại. */
export const subIndicators: string[] = ["MACD", "RSI"];

/** Thay toàn bộ danh sách chỉ báo pane phụ (dùng khi khôi phục từ chart). */
export function replaceSubIndicators(names: readonly string[]): void {
  subIndicators.splice(0, subIndicators.length, ...names);
}

/** Cầu nối tới chart đang hiển thị. */
export interface ChartBridge {
  /** Vẽ lại chart để áp dụng cài đặt đường của indicator. */
  repaint: () => void;
  /** Bật/tắt chỉ báo trên pane giá; cần dựng lại chart nên tốn hơn `repaint`. */
  toggleMain: (name: string, enabled: boolean) => void;
}

let bridge: ChartBridge | null = null;

/** Cài đặt cầu nối; gọi với `null` khi chart bị huỷ. */
export function setChartBridge(value: ChartBridge | null): void {
  bridge = value;
}

/** Yêu cầu vẽ lại chart (dùng cho tick bật/tắt đường Ichimoku). */
export function requestRepaint(): void {
  bridge?.repaint();
}

/** Bật/tắt chỉ báo trên pane giá. */
export function requestMainToggle(name: string, enabled: boolean): void {
  bridge?.toggleMain(name, enabled);
}

/** Tên indicator trên pane giá theo lựa chọn hiện tại. */
export function mainIndicatorNames(): string[] {
  return [...mainIndicators];
}
