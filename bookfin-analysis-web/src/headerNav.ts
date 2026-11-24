import type { KLineChartPro } from "@klinecharts/pro";
import { openWatchlistModal } from "./watchlistModal";

const ATTACHED_ATTR = "data-bookfin-nav-attached";

export function installHeaderNav(getChart: () => KLineChartPro | null, onClearOverlays: () => void): void {
  const checkAndInject = () => {
    const periodBar = document.querySelector<HTMLElement>(".klinecharts-pro-period-bar");
    if (!periodBar || periodBar.hasAttribute(ATTACHED_ATTR)) return;

    periodBar.setAttribute(ATTACHED_ATTR, "true");

    // Tạo Selectbox menu cho các tính năng BookFin bên góc phải
    const selectWrap = document.createElement("div");
    selectWrap.className = "bookfin-header-select-wrap";

    const select = document.createElement("select");
    select.className = "bookfin-header-select";
    select.title = "Tính năng mở rộng BookFin";

    select.innerHTML = `
      <option value="" disabled selected>Features</option>
      <option value="screener">Filter Money</option>
      <option value="chat">Chat AI · Knowledge</option>
      <option value="watchlist">Watchlist</option>
      <option value="clear_overlays">Clear Drawings</option>
      <option value="ai" disabled>AI Analysis (Coming soon)</option>
    `;

    select.addEventListener("change", () => {
      const val = select.value;
      // Reset lại về placeholder sau khi chọn để có thể chọn lại
      select.value = "";

      if (val === "screener") {
        window.location.href = "/screener";
      } else if (val === "chat") {
        window.location.href = "/chat";
      } else if (val === "watchlist") {
        const chart = getChart();
        if (chart) {
          void openWatchlistModal(chart);
        }
      } else if (val === "clear_overlays") {
        onClearOverlays();
      }
    });

    selectWrap.appendChild(select);

    // Thêm trực tiếp vào cuối cùng của period-bar (ở góc phải ngoài cùng)
    // Giữ nguyên toàn bộ vị trí các nút gốc của KLineChart Pro bên trái
    periodBar.appendChild(selectWrap);
  };

  const observer = new MutationObserver(() => {
    checkAndInject();
  });

  observer.observe(document.body, { childList: true, subtree: true });
  checkAndInject();
}
