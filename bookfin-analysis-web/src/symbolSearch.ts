/**
 * Bật tìm mã theo thời gian thực cho modal symbol search của KLineChart Pro.
 *
 * Input của Pro chỉ lắng nghe sự kiện `change`, vốn chỉ bắn khi rời ô nhập, nên
 * người dùng gõ mã xong danh sách vẫn giữ nguyên. Module này theo dõi modal và
 * chuyển mỗi lần gõ (`input`) thành `change` để tìm ngay.
 */

const INPUT_SELECTOR = ".klinecharts-pro-symbol-search-modal-input input.value";
const ATTACHED = "data-bookfin-live-search";

function attach(input: HTMLInputElement): void {
  if (input.hasAttribute(ATTACHED)) return;
  input.setAttribute(ATTACHED, "1");
  input.addEventListener("input", () => {
    // Pro đọc giá trị từ chính input nên chỉ cần phát lại `change`.
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** Theo dõi modal symbol search và gắn tìm kiếm trực tiếp vào input. */
export function installLiveSymbolSearch(): void {
  const observer = new MutationObserver(() => {
    const input = document.querySelector<HTMLInputElement>(INPUT_SELECTOR);
    if (input) attach(input);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
