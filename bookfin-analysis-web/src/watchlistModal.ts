import type { KLineChartPro, SymbolInfo } from "@klinecharts/pro";
import { apiFetch } from "./apiAuth";
import { watchlistApi } from "./screener/api";
import type { WatchlistOut } from "./screener/types";

interface SymbolMeta {
  company_name?: string;
  exchange?: string;
  price?: number;
  change_percent?: number;
}

let activeModal: HTMLElement | null = null;
const metaCache = new Map<string, SymbolMeta>();

function formatPrice(value?: number): string {
  if (value === undefined || value === null) return "—";
  return new Intl.NumberFormat("vi-VN").format(Math.round(value));
}

function formatPercent(value?: number): string {
  if (value === undefined || value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

async function fetchSymbolsMeta(symbols: string[]): Promise<void> {
  const missing = symbols.filter((s) => !metaCache.has(s) || metaCache.get(s)?.company_name === undefined);
  if (missing.length > 0) {
    try {
      const res = await apiFetch(`/api/v1/market/symbols?limit=1000`);
      if (res.ok) {
        const data = await res.json();
        for (const item of data.items || []) {
          const prev = metaCache.get(item.symbol) || {};
          metaCache.set(item.symbol, {
            ...prev,
            company_name: item.company_name,
            exchange: item.exchange,
          });
        }
      }
    } catch (e) {
      console.warn("fetchSymbolsMeta error", e);
    }
  }

  // Fetch latest quote for symbols
  await Promise.all(
    symbols.map(async (symbol) => {
      try {
        const qRes = await apiFetch(`/api/v1/market/quotes/${encodeURIComponent(symbol)}`);
        if (qRes.ok) {
          const q = await qRes.json();
          const prev = metaCache.get(symbol) || {};
          metaCache.set(symbol, {
            ...prev,
            price: q.price,
            change_percent: q.change_percent,
          });
        }
      } catch {
        // ignore individual quote fail
      }
    }),
  );
}

export async function openWatchlistModal(chart: KLineChartPro): Promise<void> {
  if (activeModal) {
    activeModal.remove();
    activeModal = null;
  }

  const modal = document.createElement("div");
  modal.className = "klinecharts-pro-modal bookfin-watchlist-modal";
  activeModal = modal;

  modal.innerHTML = `
    <div class="inner" style="width: min(560px, calc(100vw - 32px)); max-height: 80vh; display: flex; flex-direction: column; background: #ffffff; border-radius: 8px; box-shadow: 0 12px 32px rgba(0,0,0,0.12); overflow: hidden;">
      <!-- Header -->
      <div style="display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 1px solid #ebedf1;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 15px; font-weight: 700; color: #051441;">Watchlist</span>
          <span class="watchlist-count-badge" style="font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #f1f5f9; color: #475569; font-weight: 600;">...</span>
        </div>
        <button type="button" class="close-btn" data-close style="background: none; border: none; cursor: pointer; color: #94a3b8; font-size: 18px; line-height: 1; padding: 4px; border-radius: 4px;">×</button>
      </div>

      <!-- Controls bar -->
      <div style="padding: 10px 20px; border-bottom: 1px solid #f1f5f9; display: flex; align-items: center; gap: 10px; background: #f8fafc;">
        <select id="watchlist-select" class="select-control" style="flex: 1; height: 30px; border: 1px solid #cbd5e1; border-radius: 4px; padding: 0 8px; font-size: 12.5px; color: #0f172a; background: #ffffff; outline: none; cursor: pointer;">
          <option value="">Đang tải danh sách...</option>
        </select>
        <a href="/screener" target="_blank" style="font-size: 12px; color: #1677ff; text-decoration: none; font-weight: 600; padding: 5px 10px; border-radius: 4px; background: #eff6ff; border: 1px solid #bfdbfe;">
          Quản lý Watchlist
        </a>
      </div>

      <!-- Body -->
      <div class="watchlist-modal-body" style="flex: 1; overflow-y: auto; padding: 8px 16px; min-height: 200px;">
        <div id="watchlist-items-container" style="display: flex; flex-direction: column; gap: 4px;">
          <div style="text-align: center; color: #94a3b8; padding: 36px 0; font-size: 13px;">Đang tải dữ liệu...</div>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  const closeBtn = modal.querySelector("[data-close]");
  const closeModal = () => {
    modal.remove();
    activeModal = null;
  };
  closeBtn?.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  const select = modal.querySelector("#watchlist-select") as HTMLSelectElement;
  const itemsContainer = modal.querySelector("#watchlist-items-container") as HTMLElement;
  const countBadge = modal.querySelector(".watchlist-count-badge") as HTMLElement;

  let watchlists: WatchlistOut[] = [];

  try {
    const listRes = await watchlistApi.list();
    watchlists = listRes.items || [];
    if (watchlists.length === 0) {
      select.innerHTML = `<option value="">Chưa có watchlist nào</option>`;
      itemsContainer.innerHTML = `<div style="text-align: center; color: #94a3b8; padding: 36px 0; font-size: 13px;">Chưa có mã trong Watchlist. Vào trang <a href="/screener" style="color:#1677ff">Bộ lọc Screener</a> để thêm mã.</div>`;
      countBadge.textContent = "0 mã";
      return;
    }

    select.innerHTML = watchlists
      .map(
        (w) =>
          `<option value="${w.id}">${w.name} (${w.item_count ?? 0} mã)</option>`,
      )
      .join("");

    const loadItems = async (wlId: number) => {
      itemsContainer.innerHTML = `<div style="text-align: center; color: #94a3b8; padding: 36px 0; font-size: 13px;">Đang tải danh sách...</div>`;
      try {
        const itemRes = await watchlistApi.items(wlId);
        const items = itemRes.items || [];
        countBadge.textContent = `${items.length} mã`;

        if (items.length === 0) {
          itemsContainer.innerHTML = `<div style="text-align: center; color: #94a3b8; padding: 36px 0; font-size: 13px;">Danh sách trống.<br>Hãy thêm mã từ trang <a href="/screener" style="color:#1677ff; text-decoration: underline;">Filter Money</a>.</div>`;
          return;
        }

        const symList = items.map((i) => i.symbol);
        await fetchSymbolsMeta(symList);

        const currentTicker = chart.getSymbol()?.ticker;

        itemsContainer.innerHTML = items
          .map((item) => {
            const meta = metaCache.get(item.symbol);
            const isCurrent = item.symbol === currentTicker;
            const change = meta?.change_percent ?? 0;
            const changeColor = change > 0 ? "#16a34a" : change < 0 ? "#dc2626" : "#475569";
            const changeBg = change > 0 ? "#f0fdf4" : change < 0 ? "#fef2f2" : "#f8fafc";

            return `
              <div class="watchlist-modal-row ${isCurrent ? "current" : ""}" data-symbol="${item.symbol}" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-radius: 6px; border: 1px solid ${isCurrent ? "#93c5fd" : "#f1f5f9"}; background: ${isCurrent ? "#eff6ff" : "#ffffff"}; cursor: pointer; transition: all 0.15s ease;">
                <div style="display: flex; align-items: center; gap: 10px; min-width: 0;">
                  <span style="font-weight: 700; font-size: 14.5px; color: ${isCurrent ? "#1d4ed8" : "#0f172a"}; width: 48px;">${item.symbol}</span>
                  <span style="font-size: 11px; padding: 1px 5px; border-radius: 3px; background: #f1f5f9; color: #64748b; font-weight: 600;">${meta?.exchange ?? "HOSE"}</span>
                  <span style="font-size: 12px; color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px;">${meta?.company_name ?? ""}</span>
                </div>
                
                <div style="display: flex; align-items: center; gap: 12px; flex-shrink: 0;">
                  ${meta?.price ? `<span style="font-size: 13px; font-weight: 700; color: #0f172a;">${formatPrice(meta.price)}</span>` : ""}
                  ${
                    meta?.change_percent !== undefined
                      ? `<span style="font-size: 11.5px; font-weight: 700; padding: 2px 6px; border-radius: 4px; color: ${changeColor}; background: ${changeBg};">${formatPercent(meta.change_percent)}</span>`
                      : ""
                  }
                  ${isCurrent ? `<span style="font-size: 11px; color: #2563eb; font-weight: 600;">Đang xem</span>` : ""}
                </div>
              </div>
            `;
          })
          .join("");

        itemsContainer.querySelectorAll<HTMLElement>(".watchlist-modal-row").forEach((row) => {
          row.addEventListener("mouseenter", () => {
            if (!row.classList.contains("current")) {
              row.style.background = "#f8fafc";
              row.style.borderColor = "#cbd5e1";
            }
          });
          row.addEventListener("mouseleave", () => {
            if (!row.classList.contains("current")) {
              row.style.background = "#ffffff";
              row.style.borderColor = "#f1f5f9";
            }
          });
          row.addEventListener("click", () => {
            const symbol = row.dataset.symbol;
            if (symbol) {
              const meta = metaCache.get(symbol);
              const symbolInfo: SymbolInfo = {
                ticker: symbol,
                name: meta?.company_name ?? symbol,
                shortName: symbol,
                exchange: meta?.exchange ?? "HOSE",
                market: "stocks",
                priceCurrency: "VND",
                pricePrecision: 2,
                volumePrecision: 0,
              };
              chart.setSymbol(symbolInfo);
              closeModal();
            }
          });
        });
      } catch (err) {
        itemsContainer.innerHTML = `<div style="text-align: center; color: #ef4444; padding: 36px 0; font-size: 13px;">Không tải được danh sách mã.</div>`;
      }
    };

    select.addEventListener("change", () => {
      const val = Number(select.value);
      if (val) loadItems(val);
    });

    if (watchlists[0]) {
      loadItems(watchlists[0].id);
    }
  } catch (err) {
    select.innerHTML = `<option value="">Lỗi kết nối</option>`;
    itemsContainer.innerHTML = `<div style="text-align: center; color: #ef4444; padding: 36px 0; font-size: 13px;">Lỗi kết nối tới hệ thống Watchlist.</div>`;
  }
}
