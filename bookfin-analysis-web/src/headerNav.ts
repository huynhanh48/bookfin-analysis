import type { KLineChartPro } from "@klinecharts/pro";
import { openWatchlistModal } from "./watchlistModal";

const BTN_ID = "bookfin-drawingbar-switcher-btn";
const MODAL_ID = "bookfin-switcher-modal";

const SETTING_LABEL_MAP: Record<string, string> = {
  candle_type: "Candle Type",
  last_price_show: "Last Price",
  high_price_show: "High Price",
  low_price_show: "Low Price",
  indicator_last_value_show: "Indicator Value",
  price_axis_type: "Price Axis",
  reverse_coordinate: "Reverse Axis",
  grid_show: "Grid Lines",
  restore_default: "Reset Default",
  "OHLC: đổi màu": "OHLC: Color",
  "OHLC màu đen": "OHLC Black",
  normal: "Normal",
  percentage: "Percentage",
  log: "Logarithmic",
};

function normalizeSettingModal(): void {
  const modal = document.querySelector<HTMLElement>(".klinecharts-pro-setting-modal, .klinecharts-pro-modal");
  if (!modal) return;

  const walker = document.createTreeWalker(modal, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const raw = node.textContent?.trim();
    if (raw && SETTING_LABEL_MAP[raw]) {
      node.textContent = SETTING_LABEL_MAP[raw];
    }
  }
}

function openSwitcherModal(getChart: () => KLineChartPro | null, onClearOverlays: () => void): void {
  document.getElementById(MODAL_ID)?.remove();

  const overlay = document.createElement("div");
  overlay.id = MODAL_ID;
  overlay.className = "bookfin-switcher-overlay";
  overlay.innerHTML = `
    <div class="bookfin-switcher-card" role="dialog" aria-modal="true" aria-label="BookFin Navigation">
      <div class="bookfin-switcher-head">
        <div class="bookfin-switcher-brand">
          <span class="bookfin-switcher-badge">B</span>
          <span>BookFin Workspace</span>
        </div>
        <button type="button" class="bookfin-switcher-close" aria-label="Close">×</button>
      </div>
      <div class="bookfin-switcher-list">
        <button type="button" class="bookfin-switcher-item" data-switch="screener">
          <span class="bookfin-switcher-icon blue">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          </span>
          <span class="bookfin-switcher-meta">
            <strong>Filter Money</strong>
            <small>Stock screener, divergence &amp; volume signals</small>
          </span>
        </button>
        <button type="button" class="bookfin-switcher-item" data-switch="watchlist">
          <span class="bookfin-switcher-icon amber">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
          </span>
          <span class="bookfin-switcher-meta">
            <strong>Watchlist</strong>
            <small>Quick switch symbols from your saved lists</small>
          </span>
        </button>
        <button type="button" class="bookfin-switcher-item" data-switch="chat">
          <span class="bookfin-switcher-icon emerald">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          </span>
          <span class="bookfin-switcher-meta">
            <strong>Chat AI · Knowledge</strong>
            <small>Ask RAG documents or run AI stock research</small>
          </span>
        </button>
        <div class="bookfin-switcher-divider"></div>
        <button type="button" class="bookfin-switcher-item danger" data-switch="clear_overlays">
          <span class="bookfin-switcher-icon red">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
          </span>
          <span class="bookfin-switcher-meta">
            <strong>Clear Drawings</strong>
            <small>Remove all drawn lines and shapes on chart</small>
          </span>
        </button>
      </div>
    </div>
  `;

  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKeyDown);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") close();
  };

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  overlay.querySelector(".bookfin-switcher-close")?.addEventListener("click", close);

  overlay.querySelectorAll<HTMLButtonElement>("[data-switch]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.switch;
      close();
      if (action === "screener") {
        window.location.href = "/screener";
      } else if (action === "chat") {
        window.location.href = "/chat";
      } else if (action === "watchlist") {
        const chart = getChart();
        if (chart) void openWatchlistModal(chart);
      } else if (action === "clear_overlays") {
        onClearOverlays();
      }
    });
  });

  document.addEventListener("keydown", onKeyDown);
  document.body.appendChild(overlay);
}

export function installHeaderNav(getChart: () => KLineChartPro | null, onClearOverlays: () => void): void {
  const injectDrawingBarIcon = () => {
    const drawingBar = document.querySelector<HTMLElement>(".klinecharts-pro-drawing-bar");
    if (!drawingBar || document.getElementById(BTN_ID)) return;

    const wrap = document.createElement("div");
    wrap.id = BTN_ID;
    wrap.className = "bookfin-drawingbar-switcher-wrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bookfin-drawingbar-switcher-btn";
    btn.title = "BookFin Workspace (Filter Money, Watchlist, Chat AI)";
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>`;

    btn.addEventListener("click", () => {
      openSwitcherModal(getChart, onClearOverlays);
    });

    wrap.appendChild(btn);
    drawingBar.appendChild(wrap);
  };

  const observer = new MutationObserver(() => {
    injectDrawingBarIcon();
    normalizeSettingModal();
  });

  observer.observe(document.body, { childList: true, subtree: true });
  injectDrawingBarIcon();
  normalizeSettingModal();
}
