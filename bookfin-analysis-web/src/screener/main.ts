import "./styles.css";
import { screenerApi, watchlistApi } from "./api";
import { ScreenerSocket, type ScreenerUpdate } from "./realtime";
import type {
  DivergenceCondition,
  ScreenerConditions,
  ScreenerFeedStatus,
  ScreenerRow,
  WatchlistItemOut,
  WatchlistOut,
} from "./types";

const DEFAULT_LIMIT = 1000;
const priceFormat = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
const compactFormat = new Intl.NumberFormat("vi-VN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

interface ViewState {
  conditions: ScreenerConditions;
  activePreset: string;
  results: ScreenerRow[];
  selected: Set<string>;
  search: string;
  sortKey: keyof ScreenerRow;
  sortDir: "asc" | "desc";
  realtime: boolean;
  isLoading: boolean;
  scanError: string | null;
  profileId: string | null;
  feedStatus: ScreenerFeedStatus;
  updatedAt: string | null;
  evaluated: number;
  skipped: number;
  watchlists: WatchlistOut[];
  activeWatchlistId: number | null;
  watchlistItems: WatchlistItemOut[];
}

const PRESETS: Array<{ id: string; name: string; desc: string; conditions: ScreenerConditions }> = [
  {
    id: "divergence",
    name: "Divergence",
    desc: "Bộ lọc riêng: phân kỳ đã xác nhận trên phiên gần nhất hoặc N phiên gần nhất",
    conditions: {
      min_volume: { enabled: false, value: 100_000 },
      min_price: { enabled: false, value: 10_000 },
      relative_volume: { enabled: false, value: 0.8, lookback: 10 },
      price_change: { enabled: false, percent: 2 },
      price_vs_ma5: { enabled: false, percent: 2, period: 5 },
      divergence: {
        enabled: true,
        direction: "any",
        kind: "any",
        window: "latest",
        days: 5,
        basis: "pivot",
        date_from: null,
        date_to: null,
      },
    },
  },
  {
    id: "breakout_volume",
    name: "Filter Money",
    desc: "Khối lượng bùng nổ > 1.5x SMA10, giá tăng trên 2%, vượt MA5",
    conditions: {
      min_volume: { enabled: true, value: 200_000 },
      min_price: { enabled: true, value: 10_000 },
      relative_volume: { enabled: true, value: 1.5, lookback: 10 },
      price_change: { enabled: true, percent: 2 },
      price_vs_ma5: { enabled: true, percent: 1.5, period: 5 },
      divergence: {
        enabled: false,
        direction: "bullish",
        kind: "regular",
        window: "latest",
        days: 1,
        basis: "pivot",
        date_from: null,
        date_to: null,
      },
    },
  },
  {
    id: "strong_uptrend",
    name: "Tăng trưởng mạnh mẽ",
    desc: "Giá tăng trên 3%, duy trì trên MA5, thanh khoản ổn định",
    conditions: {
      min_volume: { enabled: true, value: 150_000 },
      min_price: { enabled: true, value: 12_000 },
      relative_volume: { enabled: true, value: 1.1, lookback: 10 },
      price_change: { enabled: true, percent: 3 },
      price_vs_ma5: { enabled: true, percent: 2, period: 5 },
      divergence: {
        enabled: false,
        direction: "bullish",
        kind: "regular",
        window: "latest",
        days: 1,
        basis: "pivot",
        date_from: null,
        date_to: null,
      },
    },
  },
  {
    id: "deep_value",
    name: "Thanh khoản cao & Tích luỹ",
    desc: "Volume cực lớn (>500k), giá giữ nền vững quanh MA5",
    conditions: {
      min_volume: { enabled: true, value: 500_000 },
      min_price: { enabled: true, value: 10_000 },
      relative_volume: { enabled: true, value: 0.9, lookback: 10 },
      price_change: { enabled: true, percent: 0.5 },
      price_vs_ma5: { enabled: true, percent: 0.5, period: 5 },
      divergence: {
        enabled: false,
        direction: "bullish",
        kind: "regular",
        window: "latest",
        days: 1,
        basis: "pivot",
        date_from: null,
        date_to: null,
      },
    },
  },
  {
    id: "custom",
    name: "Tự tuỳ chỉnh điều kiện",
    desc: "Điều kiện lọc do bạn trực tiếp thiết lập",
    conditions: {
      min_volume: { enabled: true, value: 100_000 },
      min_price: { enabled: true, value: 10_000 },
      relative_volume: { enabled: true, value: 0.8, lookback: 10 },
      price_change: { enabled: true, percent: 2 },
      price_vs_ma5: { enabled: true, percent: 2, period: 5 },
      divergence: {
        enabled: false,
        direction: "bullish",
        kind: "regular",
        window: "latest",
        days: 1,
        basis: "pivot",
        date_from: null,
        date_to: null,
      },
    },
  },
];

const state: ViewState = {
  conditions: { ...PRESETS[0].conditions },
  activePreset: "divergence",
  results: [],
  selected: new Set(),
  search: "",
  sortKey: "symbol",
  sortDir: "asc",
  realtime: false,
  isLoading: false,
  scanError: null,
  profileId: null,
  feedStatus: "OFFLINE",
  updatedAt: null,
  evaluated: 0,
  skipped: 0,
  watchlists: [],
  activeWatchlistId: null,
  watchlistItems: [],
};

let socket: ScreenerSocket | null = null;

function esc(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setStatus(message: string, level: "ok" | "error" = "ok"): void {
  const el = document.getElementById("screener-status");
  if (!el) return;
  el.textContent = message;
  el.dataset.level = level;
}

function formatPrice(value: number): string {
  return Number.isFinite(value) ? priceFormat.format(value) : "—";
}

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("vi-VN");
}

function splitSymbols(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function renderPresetsSidebar(): string {
  return `
    <div class="sidebar-section-title">
      <span>Mẫu lọc nổi bật</span>
    </div>
    <ul class="presets-list" id="presets-list">
      ${PRESETS.map(
    (p) => `
        <li class="preset-item ${p.id === state.activePreset ? "active" : ""}" data-preset-id="${p.id}">
          <span title="${esc(p.desc)}">${esc(p.name)}</span>
        </li>`,
  ).join("")}
    </ul>
    <div class="sidebar-divider"></div>
  `;
}

function renderFilterChips(): string {
  const c = state.conditions;
  const chips: string[] = [];

  if (c.min_volume.enabled) {
    chips.push(`
      <div class="filter-chip active" data-chip="min_volume">
        <span class="filter-chip-text">📊 KL: ≥ ${compactFormat.format(c.min_volume.value)}</span>
        <span class="filter-chip-edit-icon" data-open-modal="min_volume" title="Chỉnh sửa thông số">✎</span>
        <span class="filter-chip-remove" data-remove-chip="min_volume" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="min_volume">
        <span class="filter-chip-text">+ KL tối thiểu</span>
      </div>
    `);
  }

  if (c.min_price.enabled) {
    chips.push(`
      <div class="filter-chip active" data-chip="min_price">
        <span class="filter-chip-text">💵 Giá: ≥ ${priceFormat.format(c.min_price.value)}</span>
        <span class="filter-chip-edit-icon" data-open-modal="min_price" title="Chỉnh sửa thông số">✎</span>
        <span class="filter-chip-remove" data-remove-chip="min_price" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="min_price">
        <span class="filter-chip-text">+ Giá tối thiểu</span>
      </div>
    `);
  }

  if (c.relative_volume.enabled) {
    chips.push(`
      <div class="filter-chip active" data-chip="relative_volume">
        <span class="filter-chip-text">🔥 Vol / SMA10: ≥ ${c.relative_volume.value}x</span>
        <span class="filter-chip-edit-icon" data-open-modal="relative_volume" title="Chỉnh sửa thông số">✎</span>
        <span class="filter-chip-remove" data-remove-chip="relative_volume" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="relative_volume">
        <span class="filter-chip-text">+ Đột biến Vol/SMA10</span>
      </div>
    `);
  }

  if (c.price_change.enabled) {
    chips.push(`
      <div class="filter-chip active" data-chip="price_change">
        <span class="filter-chip-text">📈 Tăng giá: ≥ ${c.price_change.percent}%</span>
        <span class="filter-chip-edit-icon" data-open-modal="price_change" title="Chỉnh sửa thông số">✎</span>
        <span class="filter-chip-remove" data-remove-chip="price_change" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="price_change">
        <span class="filter-chip-text">+ Tăng giá %</span>
      </div>
    `);
  }

  if (c.price_vs_ma5.enabled) {
    chips.push(`
      <div class="filter-chip active" data-chip="price_vs_ma5">
        <span class="filter-chip-text">⚡ Vượt MA5: ≥ ${c.price_vs_ma5.percent}%</span>
        <span class="filter-chip-edit-icon" data-open-modal="price_vs_ma5" title="Chỉnh sửa thông số">✎</span>
        <span class="filter-chip-remove" data-remove-chip="price_vs_ma5" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="price_vs_ma5">
        <span class="filter-chip-text">+ Giá vs MA5</span>
      </div>
    `);
  }

  // DIVERGENCE CHIP
  if (c.divergence.enabled) {
    const divDir = c.divergence.direction === "bullish" ? "Bull" : c.divergence.direction === "bearish" ? "Bear" : "Bull/Bear";
    const divKind = c.divergence.kind === "regular" ? "Thường" : c.divergence.kind === "hidden" ? "Ẩn" : "Any";
    const divWin = c.divergence.window === "range"
      ? `${c.divergence.date_from || "—"} → ${c.divergence.date_to || "—"}`
      : `${c.divergence.days || 1} phiên`;
    chips.push(`
      <div class="filter-chip active" data-chip="divergence" style="background:#eff6ff; border-color:#93c5fd; color:#1e40af;">
        <span class="filter-chip-text">📈 Divergence: ${divDir} · ${divKind} · ${divWin} · ${c.divergence.basis}</span>
        <span class="filter-chip-edit-icon" data-open-modal="divergence" title="Cài đặt phân kỳ">✎</span>
        <span class="filter-chip-remove" data-remove-chip="divergence" title="Tắt điều kiện">×</span>
      </div>
    `);
  } else {
    chips.push(`
      <div class="filter-chip" data-chip="divergence">
        <span class="filter-chip-text">+ Divergence (Phân kỳ)</span>
      </div>
    `);
  }

  return chips.join("");
}

function shell(): string {
  const activePresetObj = PRESETS.find((p) => p.id === state.activePreset) || PRESETS[0];
  return `
  <div class="screener-shell">
    <header class="screener-topbar">
      <div class="screener-brand">
        <span class="screener-brand-badge">PRO</span>
        <span>BookFin Analysis</span>
      </div>
      <nav class="screener-nav">
        <a href="/">Biểu đồ</a>
        <a href="/screener" class="active">Lọc realtime</a>
      </nav>
      <span class="spacer"></span>
      <div class="screener-status-badge">
        <span class="status-dot ${state.realtime ? "pulse-live" : ""}"></span>
        <span id="screener-status">Sẵn sàng</span>
      </div>
    </header>

    <main class="screener-body">
      <!-- SIDEBAR (FireAnt style) -->
      <aside class="screener-panel watchlist-panel">
        ${renderPresetsSidebar()}
        <div class="sidebar-section-title">
          <span>Watchlist của tôi</span>
          <button class="btn icon" id="new-watchlist" title="Tạo watchlist mới">+</button>
        </div>
        <ul class="watchlist-list" id="watchlist-list"></ul>
        <div class="watchlist-items-box">
          <div class="watchlist-items-head">Mã trong watchlist</div>
          <div class="watchlist-items" id="watchlist-items"></div>
        </div>
      </aside>

      <!-- RIGHT PANEL (FireAnt Header + Filter Chips + Results Table) -->
      <section class="screener-panel results-panel">
        <div class="screener-header-area">
          <div class="screener-title-row">
            <div class="screener-title-group">
              <h2 class="screener-profile-name" id="screener-title">${esc(activePresetObj.name)}</h2>
              <span class="screener-match-count" id="match-count">${state.results.length}</span>
              <span class="screener-match-meta">mã khớp</span>
            </div>
            <div class="screener-header-actions">
              <div class="filter-universe">
                <select id="universe" class="select-control" style="width:145px">
                  <option value="all">Toàn thị trường</option>
                  <option value="symbols">Danh sách mã</option>
                </select>
                <input type="text" id="symbols" class="input-control" placeholder="FPT, HPG, SSI" style="display:none; width:135px" />
              </div>
              <label class="toggle" style="font-size:12.5px; font-weight:600; cursor:pointer">
                <input type="checkbox" id="realtime" ${state.realtime ? "checked" : ""}/> Realtime
              </label>
              <button class="btn" id="reset" title="Đặt lại bộ lọc">Mặc định</button>
              <button class="btn primary" id="scan">Quét ngay</button>
            </div>
          </div>

          <!-- FIREANT FILTER CHIPS -->
          <div class="filter-chips-container" id="filter-chips">
            ${renderFilterChips()}
          </div>
        </div>

        <!-- RESULTS TOOLBAR -->
        <div class="results-toolbar">
          <div class="tab-pill-group">
            <button id="screener-results-tab" class="tab-pill active">Kết quả (${state.results.length})</button>
          </div>
          <span class="results-summary" id="results-summary" style="font-size:12px; color:var(--muted)">Chưa quét</span>
          <span class="spacer"></span>
          <div class="search-input-wrap">
            <span class="search-icon">🔍</span>
            <input type="text" id="result-search" placeholder="Lọc mã trong bảng…" />
          </div>
          <select id="target-watchlist" class="select-control" style="width:145px"></select>
          <button class="btn" id="add-selected">Thêm mã chọn</button>
          <button class="btn" id="add-all">Thêm tất cả</button>
          <button class="btn" id="column-settings" type="button">⚙ Cột hiển thị</button>
          <dialog id="column-settings-dialog" aria-label="Cột hiển thị">
            <form method="dialog" class="screener-column-settings">
              <h3>Cột hiển thị</h3>
              ${optionalColumns.map(([index, label]) =>
                `<label><input type="checkbox" data-column="${index}" ${hiddenColumns.has(index) ? "" : "checked"}> ${label}</label>`
              ).join("")}
              <button class="btn" type="submit">Xong</button>
            </form>
          </dialog>
        </div>

        <!-- DATA TABLE -->
        <div class="results-table-wrap table-loading-overlay">
          <div class="table-loading-bar" id="table-progress-bar" style="display:none;"></div>
          <table class="results">
            <thead>
              <tr>
                <th style="width:36px"><input type="checkbox" id="select-all" /></th>
                <th data-sort="symbol"><button class="screener-sort" type="button">Mã CP<span aria-hidden="true"></span></button></th>
                <th data-sort="price"><button class="screener-sort" type="button">Giá (VND)<span aria-hidden="true"></span></button></th>
                <th data-sort="change_percent"><button class="screener-sort" type="button">Biến động<span aria-hidden="true"></span></button></th>
                <th data-sort="volume"><button class="screener-sort" type="button">Tổng Khối Lượng<span aria-hidden="true"></span></button></th>
                <th data-sort="relative_volume"><button class="screener-sort" type="button">Vol/SMA10<span aria-hidden="true"></span></button></th>
                <th data-sort="ma5"><button class="screener-sort" type="button">Giá MA5<span aria-hidden="true"></span></button></th>
                <th data-sort="divergence"><button class="screener-sort" type="button">Divergence<span aria-hidden="true"></span></button></th>
                <th data-sort="status"><button class="screener-sort" type="button">Trạng thái<span aria-hidden="true"></span></button></th>
                <th style="width:70px">Thao tác</th>
              </tr>
            </thead>
            <tbody id="results-body"></tbody>
          </table>
        </div>
      </section>
    </main>
  </div>`;
}

function renderWatchlists(): void {
  const list = document.getElementById("watchlist-list");
  const target = document.getElementById("target-watchlist");
  if (!list || !target) return;
  list.innerHTML =
    state.watchlists
      .map(
        (watchlist) => `
        <li data-id="${watchlist.id}" class="${watchlist.id === state.activeWatchlistId ? "active" : ""}">
          <span class="watchlist-name" data-role="name">${esc(watchlist.name)}</span>
          <span class="watchlist-count">${watchlist.item_count}</span>
          <span class="watchlist-actions">
            <button class="btn icon" data-action="rename" title="Đổi tên">✎</button>
            <button class="btn icon" data-action="clear" title="Làm rỗng">⌫</button>
            ${watchlist.is_default ? "" : '<button class="btn icon danger" data-action="delete" title="Xoá">✕</button>'}
          </span>
        </li>`,
      )
      .join("") || '<li class="watchlist-empty">Chưa có watchlist</li>';
  target.innerHTML = state.watchlists
    .map(
      (watchlist) =>
        `<option value="${watchlist.id}" ${watchlist.id === state.activeWatchlistId ? "selected" : ""}>${esc(watchlist.name)}</option>`,
    )
    .join("");
}

function renderWatchlistItems(): void {
  const el = document.getElementById("watchlist-items");
  if (!el) return;
  if (!state.activeWatchlistId) {
    el.innerHTML = '<div class="watchlist-empty">Chọn một watchlist</div>';
    return;
  }
  if (state.watchlistItems.length === 0) {
    el.innerHTML = '<div class="watchlist-empty">Danh sách rỗng</div>';
    return;
  }
  el.innerHTML = state.watchlistItems
    .map(
      (item) => `
      <div class="watchlist-item">
        <span class="symbol" data-chart="${esc(item.symbol)}">${esc(item.symbol)}</span>
        <button class="btn icon danger" data-remove-symbol="${esc(item.symbol)}" title="Xoá">✕</button>
      </div>`,
    )
    .join("");
}

const COLUMN_SETTINGS_KEY = "bookfin:screener:hidden-columns";
const optionalColumns = [
  [2, "Giá (VND)"], [3, "Biến động"], [4, "Tổng khối lượng"],
  [5, "Vol/SMA10"], [6, "Giá MA5"], [7, "Divergence"],
  [8, "Trạng thái"], [9, "Thao tác"],
] as const;

const hiddenColumns = new Set<number>();
try {
  const saved: unknown = JSON.parse(localStorage.getItem(COLUMN_SETTINGS_KEY) ?? "[]");
  if (Array.isArray(saved)) {
    for (const [index] of optionalColumns) if (saved.includes(index)) hiddenColumns.add(index);
  }
} catch { /* Use default columns when storage is unavailable. */ }

function applyResultColumns(): void {
  document.querySelectorAll<HTMLTableRowElement>(".results tr").forEach((row) => {
    if (row.cells.length === 1) {
      row.cells[0].colSpan = 10 - hiddenColumns.size;
      return;
    }
    Array.from(row.cells).forEach((cell, index) => {
      cell.hidden = hiddenColumns.has(index);
    });
  });
  document.querySelectorAll<HTMLElement>(".results th[data-sort]").forEach((header) => {
    const active = header.dataset.sort === state.sortKey;
    header.setAttribute("aria-sort", active
      ? (state.sortDir === "asc" ? "ascending" : "descending") : "none");
    const arrow = header.querySelector("span");
    if (arrow) arrow.textContent = active ? (state.sortDir === "asc" ? " ↑" : " ↓") : "";
  });
}

function filteredRows(): ScreenerRow[] {
  const search = state.search.trim().toUpperCase();
  const rows = state.results.filter(
    (row) => !search || row.symbol.includes(search),
  );
  return [...rows].sort((left, right) => {
    const a = left[state.sortKey];
    const b = right[state.sortKey];
    const comparison =
      typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a ?? "").localeCompare(String(b ?? ""));
    return state.sortDir === "asc" ? comparison : -comparison;
  });
}

function renderResults(): void {
  const resultsTab = document.getElementById("screener-results-tab");
  if (resultsTab) resultsTab.textContent = `Kết quả (${state.results.length})`;
  const body = document.getElementById("results-body");
  const summary = document.getElementById("results-summary");
  const matchCount = document.getElementById("match-count");
  const progressBar = document.getElementById("table-progress-bar");
  const selectAll = document.getElementById("select-all") as HTMLInputElement | null;
  if (!body) return;
  const resultActionsDisabled = state.isLoading || state.scanError !== null;
  for (const id of ["add-selected", "add-all", "select-all"]) {
    const control = document.getElementById(id) as HTMLButtonElement | HTMLInputElement | null;
    if (control) control.disabled = resultActionsDisabled;
  }

  if (state.isLoading) {
    if (progressBar) progressBar.style.display = "block";
    if (summary) summary.textContent = "Đang quét thị trường…";
    if (matchCount) matchCount.textContent = "…";

    // Hiển thị 8 dòng Skeleton Loading hiệu ứng mượt mà
    body.innerHTML = Array.from({ length: 8 })
      .map(
        () => `
        <tr class="skeleton-row">
          <td><span class="skeleton-box" style="width:16px;"></span></td>
          <td><span class="skeleton-box" style="width:48px;"></span></td>
          <td><span class="skeleton-box" style="width:65px;"></span></td>
          <td><span class="skeleton-box" style="width:55px;"></span></td>
          <td><span class="skeleton-box" style="width:75px;"></span></td>
          <td><span class="skeleton-box" style="width:50px;"></span></td>
          <td><span class="skeleton-box" style="width:60px;"></span></td>
          <td><span class="skeleton-box" style="width:90px;"></span></td>
          <td><span class="skeleton-box" style="width:70px;"></span></td>
          <td><span class="skeleton-box" style="width:60px;"></span></td>
        </tr>`,
      )
      .join("");
    applyResultColumns();
    return;
  }

  if (progressBar) progressBar.style.display = "none";

  if (state.scanError) {
    if (summary) summary.textContent = "Quét thất bại";
    if (matchCount) matchCount.textContent = "—";
    if (resultsTab) resultsTab.textContent = "Kết quả";
    if (selectAll) selectAll.checked = false;
    body.innerHTML = `<tr><td class="empty" colspan="10" role="alert">${esc(state.scanError)}</td></tr>`;
    applyResultColumns();
    return;
  }

  const rows = filteredRows();
  if (matchCount) matchCount.textContent = String(rows.length);
  if (summary) {
    summary.textContent = `${state.results.length} mã khớp · ${state.evaluated} đã quét · ${formatTime(state.updatedAt)} · ${state.feedStatus}`;
  }
  if (rows.length === 0) {
    const hint =
      state.conditions.divergence.enabled
        ? `Không có phân kỳ đã xác nhận với pivot trong cửa số này. Pivot cần thêm 8 nến để xác nhận; thử tăng số phiên hoặc chọn mốc "Nến xác nhận".`
        : "";
    body.innerHTML = `<tr><td class="empty" colspan="10" style="text-align:center; padding:36px; color:var(--muted)">Không tìm thấy mã nào thỏa điều kiện lọc.${hint ? `<div style="font-size:11px; margin-top:6px; color:var(--muted)">${esc(hint)}</div>` : ""}</td></tr>`;
  } else {
    body.innerHTML = rows
      .map(
        (row) => `
        <tr>
          <td><input type="checkbox" data-select="${esc(row.symbol)}" ${state.selected.has(row.symbol) ? "checked" : ""}/></td>
          <td class="symbol" data-chart="${esc(row.symbol)}">${esc(row.symbol)}</td>
          <td><b>${formatPrice(row.price)}</b></td>
          <td>
            <span class="badge-percent ${row.change_percent >= 0 ? "up" : "down"}">
              ${formatPercent(row.change_percent)}
            </span>
          </td>
          <td>${compactFormat.format(row.volume)}</td>
          <td>${row.volume_sma > 0 ? `${row.relative_volume.toFixed(2)}x` : "—"}</td>
          <td>${formatPrice(row.ma5)}</td>
          <td>${row.divergence ? `<span class="badge-divergence">${esc(row.divergence)}</span>` : "—"}</td>
          <td><span class="status-badge status-${row.status}">${row.status}</span></td>
          <td><button class="btn" style="height:24px; padding:0 8px; font-size:11px" data-add="${esc(row.symbol)}">+ Watchlist</button></td>
        </tr>`,
      )
      .join("");
  }
  applyResultColumns();
  if (selectAll) {
    const visibleSelected = rows.filter((row) => state.selected.has(row.symbol)).length;
    selectAll.checked = rows.length > 0 && visibleSelected === rows.length;
    selectAll.indeterminate = visibleSelected > 0 && visibleSelected < rows.length;
  }
}

function promptNumberModal(title: string, label: string, initialValue: number, step = "1", suffix = ""): Promise<number | null> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal">
        <h3>${esc(title)}</h3>
        <p style="margin:0 0 8px; color:var(--text-secondary); font-size:12.5px">${esc(label)}</p>
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
          <input type="number" class="input-control" style="flex:1; height:34px;" value="${initialValue}" step="${step}" />
          ${suffix ? `<span style="font-weight:600; color:var(--muted)">${esc(suffix)}</span>` : ""}
        </div>
        <div class="modal-actions">
          <button class="btn" data-cancel>Huỷ</button>
          <button class="btn primary" data-confirm>Áp dụng</button>
        </div>
      </div>`;
    const input = backdrop.querySelector("input") as HTMLInputElement;
    const close = (value: number | null) => {
      backdrop.remove();
      resolve(value);
    };
    backdrop.querySelector("[data-cancel]")?.addEventListener("click", () => close(null));
    backdrop.querySelector("[data-confirm]")?.addEventListener("click", () => {
      const num = Number(input.value);
      close(Number.isFinite(num) ? num : null);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        const num = Number(input.value);
        close(Number.isFinite(num) ? num : null);
      }
      if (event.key === "Escape") close(null);
    });
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(null);
    });
    document.body.append(backdrop);
    input.focus();
    input.select();
  });
}

function divergenceModal(initial: DivergenceCondition): Promise<DivergenceCondition | null> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal" style="width:min(480px, calc(100vw - 32px))">
        <h3>Cài đặt bộ lọc Divergence (Phân kỳ)</h3>
        <p style="margin:0 0 14px; color:var(--text-secondary); font-size:12.5px">Lọc phân kỳ giá và MACD / RSI đã xác nhận trên nến đóng.</p>
        
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
          <div>
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">HƯỚNG PHÂN KỲ</label>
            <select id="modal-div-dir" class="select-control" style="width:100%">
              <option value="bullish" ${initial.direction === "bullish" ? "selected" : ""}>Tăng giá (Bullish)</option>
              <option value="bearish" ${initial.direction === "bearish" ? "selected" : ""}>Giảm giá (Bearish)</option>
              <option value="any" ${initial.direction === "any" ? "selected" : ""}>Cả hai (Any)</option>
            </select>
          </div>
          <div>
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">LOẠI PHÂN KỲ</label>
            <select id="modal-div-kind" class="select-control" style="width:100%">
              <option value="regular" ${initial.kind === "regular" ? "selected" : ""}>Thường (Regular)</option>
              <option value="hidden" ${initial.kind === "hidden" ? "selected" : ""}>Ẩn (Hidden)</option>
              <option value="any" ${initial.kind === "any" ? "selected" : ""}>Tất cả</option>
            </select>
          </div>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
          <div>
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">CỬA SỔ QUAN SÁT</label>
            <select id="modal-div-window" class="select-control" style="width:100%">
              <option value="latest" ${initial.window === "latest" ? "selected" : ""}>N phiên gần nhất</option>
              <option value="range" ${initial.window === "range" ? "selected" : ""}>Khoảng ngày tùy chọn</option>
            </select>
          </div>
          <div id="modal-div-days-wrap" style="${initial.window === "range" ? "display:none" : ""}">
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">SỐ PHIÊN GẦN NHẤT</label>
            <input type="number" id="modal-div-days" class="input-control" min="1" max="60" value="${initial.days || 5}" style="width:100%" />
          </div>
        </div>

        <div id="modal-div-range-wrap" style="display:${initial.window === "range" ? "grid" : "none"}; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
          <div>
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">TỪ NGÀY</label>
            <input type="date" id="modal-div-from" class="input-control" value="${initial.date_from || ""}" style="width:100%" />
          </div>
          <div>
            <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">ĐẾN NGÀY</label>
            <input type="date" id="modal-div-to" class="input-control" value="${initial.date_to || ""}" style="width:100%" />
          </div>
        </div>

        <div style="margin-bottom: 16px;">
          <label style="display:block; font-size:11px; font-weight:700; color:var(--muted); margin-bottom:4px">MỐC THỜI GIAN LỌC</label>
          <select id="modal-div-basis" class="select-control" style="width:100%">
            <option value="pivot" ${initial.basis === "pivot" ? "selected" : ""}>Theo ngày hình thành Pivot (Đỉnh/Đáy chart)</option>
            <option value="confirmed" ${initial.basis === "confirmed" ? "selected" : ""}>Theo ngày nến xác nhận (Confirmed Candle)</option>
          </select>
        </div>

        <div class="modal-actions">
          <button class="btn" data-cancel>Huỷ</button>
          <button class="btn primary" data-confirm>Áp dụng</button>
        </div>
      </div>`;

    const winSelect = backdrop.querySelector("#modal-div-window") as HTMLSelectElement;
    const daysWrap = backdrop.querySelector("#modal-div-days-wrap") as HTMLElement;
    const rangeWrap = backdrop.querySelector("#modal-div-range-wrap") as HTMLElement;

    winSelect.addEventListener("change", () => {
      const isRange = winSelect.value === "range";
      daysWrap.style.display = isRange ? "none" : "";
      rangeWrap.style.display = isRange ? "grid" : "none";
    });

    const close = (value: DivergenceCondition | null) => {
      backdrop.remove();
      resolve(value);
    };

    backdrop.querySelector("[data-cancel]")?.addEventListener("click", () => close(null));
    backdrop.querySelector("[data-confirm]")?.addEventListener("click", () => {
      const dir = (backdrop.querySelector("#modal-div-dir") as HTMLSelectElement).value as any;
      const kind = (backdrop.querySelector("#modal-div-kind") as HTMLSelectElement).value as any;
      const win = winSelect.value as any;
      const days = Number((backdrop.querySelector("#modal-div-days") as HTMLInputElement).value) || 5;
      const basis = (backdrop.querySelector("#modal-div-basis") as HTMLSelectElement).value as any;
      const fromVal = (backdrop.querySelector("#modal-div-from") as HTMLInputElement)?.value || null;
      const toVal = (backdrop.querySelector("#modal-div-to") as HTMLInputElement)?.value || null;

      close({
        enabled: true,
        direction: dir,
        kind,
        window: win,
        days,
        basis,
        date_from: win === "range" ? fromVal : null,
        date_to: win === "range" ? toVal : null,
      });
    });

    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(null);
    });

    document.body.append(backdrop);
  });
}

function updateFilterChips(): void {
  const container = document.getElementById("filter-chips");
  if (container) {
    container.innerHTML = renderFilterChips();
  }
}

function applyConditionsToForm(): void {
  updateFilterChips();
}

function readConditions(): ScreenerConditions {
  return {
    min_volume: {
      enabled: state.conditions.min_volume.enabled,
      value: state.conditions.min_volume.value,
    },
    min_price: {
      enabled: state.conditions.min_price.enabled,
      value: state.conditions.min_price.value,
    },
    relative_volume: {
      enabled: state.conditions.relative_volume.enabled,
      value: state.conditions.relative_volume.value,
      lookback: state.conditions.relative_volume.lookback || 10,
    },
    price_change: {
      enabled: state.conditions.price_change.enabled,
      percent: state.conditions.price_change.percent,
    },
    price_vs_ma5: {
      enabled: state.conditions.price_vs_ma5.enabled,
      percent: state.conditions.price_vs_ma5.percent,
      period: state.conditions.price_vs_ma5.period || 5,
    },
    divergence: {
      enabled: state.conditions.divergence.enabled,
      direction: state.conditions.divergence.direction,
      kind: state.conditions.divergence.kind,
      window: state.conditions.divergence.window,
      days: state.conditions.divergence.days || 5,
      basis: state.conditions.divergence.basis,
      date_from: state.conditions.divergence.date_from,
      date_to: state.conditions.divergence.date_to,
    },
  };
}

let scanQueued = false;

async function scan(): Promise<void> {
  if (state.isLoading) {
    scanQueued = true;
    return;
  }

  state.conditions = readConditions();
  const universe = (document.getElementById("universe") as HTMLSelectElement).value as
    | "all"
    | "symbols";
  const symbols = splitSymbols(
    (document.getElementById("symbols") as HTMLInputElement).value,
  );
  if (universe === "symbols" && symbols.length === 0) {
    setStatus("Nhập ít nhất một mã hoặc chọn toàn thị trường.", "error");
    return;
  }

  const mode = state.realtime ? "realtime" : "historical";
  const scanBtn = document.getElementById("scan") as HTMLButtonElement | null;
  state.scanError = null;
  state.isLoading = true;
  if (scanBtn) {
    scanBtn.classList.add("is-loading");
    scanBtn.innerHTML = '<span class="spinner"></span> Đang quét…';
    scanBtn.disabled = true;
  }
  setStatus("Đang quét dữ liệu toàn thị trường…");
  renderResults();

  try {
    const response = await screenerApi.scan({
      mode,
      universe,
      symbols: universe === "symbols" ? symbols : null,
      conditions: state.conditions,
      limit: DEFAULT_LIMIT,
    });
    if (scanQueued) return;
    state.results = response.results;
    state.selected.clear();
    state.profileId = response.profile_id;
    state.feedStatus = response.feed_status;
    state.updatedAt = response.generated_at;
    state.evaluated = response.evaluated;
    state.skipped = response.skipped;
    if (state.realtime) {
      connectRealtime(response.profile_id, response.generated_at);
    } else {
      socket?.close();
    }
    setStatus(`Đã scan ${response.evaluated} mã, ${response.matched} mã thỏa.`);
  } catch (error) {
    const message = error instanceof DOMException && error.name === "TimeoutError"
      ? "Máy chủ chưa trả kết quả sau 45 giây. Bấm Quét ngay để thử lại."
      : error instanceof Error ? error.message : "Quét thất bại.";
    if (!scanQueued) state.scanError = message;
    setStatus(message, "error");
  } finally {
    state.isLoading = false;
    if (scanBtn) {
      scanBtn.classList.remove("is-loading");
      scanBtn.innerHTML = "Quét ngay";
      scanBtn.disabled = false;
    }
    renderResults();
    if (scanQueued) {
      scanQueued = false;
      void scan();
    }
  }
}

function connectRealtime(profileId: string, scanSeedAt: string): void {
  socket?.close();
  socket = new ScreenerSocket((update) => {
    const seed = Date.parse(update.scanSeedAt ?? "");
    if (!Number.isFinite(seed) || seed < Date.parse(scanSeedAt)) return;
    handleRealtimeUpdate(update);
  }, setStatus);
  socket.connect(profileId);
}

function handleRealtimeUpdate(update: ScreenerUpdate): void {
  if (state.isLoading || !state.realtime || update.profileId !== state.profileId) return;
  if (state.updatedAt && Date.parse(update.generatedAt) < Date.parse(state.updatedAt)) return;
  state.results = update.rows;
  state.feedStatus = update.feedStatus as ScreenerFeedStatus;
  state.updatedAt = update.generatedAt;
  renderResults();
}

async function loadWatchlists(): Promise<void> {
  try {
    const response = await watchlistApi.list();
    state.watchlists = response.items;
    if (!state.activeWatchlistId && state.watchlists.length > 0) {
      state.activeWatchlistId = state.watchlists[0].id;
    }
    renderWatchlists();
    if (state.activeWatchlistId) await loadWatchlistItems(state.activeWatchlistId);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Không tải được watchlist.", "error");
  }
}

async function loadWatchlistItems(watchlistId: number): Promise<void> {
  try {
    const response = await watchlistApi.items(watchlistId);
    state.watchlistItems = response.items;
    renderWatchlistItems();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Không tải được symbol.", "error");
  }
}

async function addToWatchlist(symbols: string[]): Promise<void> {
  const target = Number(
    (document.getElementById("target-watchlist") as HTMLSelectElement).value,
  );
  if (!target || symbols.length === 0) {
    setStatus("Chọn watchlist đích và ít nhất một mã.", "error");
    return;
  }
  try {
    const result = await watchlistApi.addBulk(target, symbols);
    setStatus(`Đã thêm ${result.added} mã, bỏ qua ${result.skipped} mã trùng.`);
    state.activeWatchlistId = target;
    await loadWatchlists();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Không thêm được mã.", "error");
  }
}

function selectedSymbols(): string[] {
  return [...state.selected];
}

function openChart(symbol: string): void {
  window.location.href = `/?symbol=${encodeURIComponent(symbol)}&timeframe=1D`;
}

function confirmModal(title: string, message: string, confirmLabel = "Xác nhận"): Promise<boolean> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal">
        <h3>${esc(title)}</h3>
        <p>${esc(message)}</p>
        <div class="modal-actions">
          <button class="btn" data-cancel>Huỷ</button>
          <button class="btn primary" data-confirm>${esc(confirmLabel)}</button>
        </div>
      </div>`;
    const close = (value: boolean) => {
      backdrop.remove();
      resolve(value);
    };
    backdrop.querySelector("[data-cancel]")?.addEventListener("click", () => close(false));
    backdrop.querySelector("[data-confirm]")?.addEventListener("click", () => close(true));
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(false);
    });
    document.body.append(backdrop);
  });
}

function textModal(title: string, initial = ""): Promise<string | null> {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";
    backdrop.innerHTML = `
      <div class="modal">
        <h3>${esc(title)}</h3>
        <input type="text" maxlength="120" value="${esc(initial)}" />
        <div class="modal-actions">
          <button class="btn" data-cancel>Huỷ</button>
          <button class="btn primary" data-confirm>Lưu</button>
        </div>
      </div>`;
    const input = backdrop.querySelector("input") as HTMLInputElement;
    const close = (value: string | null) => {
      backdrop.remove();
      resolve(value);
    };
    backdrop.querySelector("[data-cancel]")?.addEventListener("click", () => close(null));
    backdrop.querySelector("[data-confirm]")?.addEventListener("click", () => close(input.value.trim() || null));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") close(input.value.trim() || null);
      if (event.key === "Escape") close(null);
    });
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close(null);
    });
    document.body.append(backdrop);
    input.focus();
    input.select();
  });
}

function bindEvents(): void {
  const columnDialog = document.querySelector<HTMLDialogElement>("#column-settings-dialog");
  document.getElementById("column-settings")?.addEventListener("click", () => columnDialog?.showModal());
  columnDialog?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !target.dataset.column) return;
    const index = Number(target.dataset.column);
    if (target.checked) hiddenColumns.delete(index);
    else hiddenColumns.add(index);
    try { localStorage.setItem(COLUMN_SETTINGS_KEY, JSON.stringify([...hiddenColumns])); }
    catch { /* The active page still keeps the selection. */ }
    const header = document.querySelector<HTMLTableRowElement>(".results thead tr");
    if (!target.checked && header?.cells[index]?.dataset.sort === state.sortKey) {
      state.sortKey = "symbol";
      state.sortDir = "asc";
    }
    renderResults();
  });
  document.getElementById("scan")?.addEventListener("click", () => void scan());
  document.getElementById("reset")?.addEventListener("click", () => {
    state.conditions = {
      min_volume: { enabled: true, value: 100_000 },
      min_price: { enabled: true, value: 10_000 },
      relative_volume: { enabled: true, value: 0.8, lookback: 10 },
      price_change: { enabled: true, percent: 2 },
      price_vs_ma5: { enabled: true, percent: 2, period: 5 },
      divergence: {
        enabled: false, direction: "any", kind: "any", window: "latest",
        days: 5, basis: "pivot", date_from: null, date_to: null,
      },
    };
    applyConditionsToForm();
    setStatus("Đã reset bộ lọc mặc định.");
  });
  document.getElementById("realtime")?.addEventListener("change", (event) => {
    state.realtime = (event.target as HTMLInputElement).checked;
    if (!state.realtime) socket?.close();
    void scan();
  });
  document.getElementById("universe")?.addEventListener("change", (event) => {
    const symbols = document.getElementById("symbols") as HTMLInputElement;
    symbols.style.display = (event.target as HTMLSelectElement).value === "symbols" ? "" : "none";
  });
  document.getElementById("result-search")?.addEventListener("input", (event) => {
    state.search = (event.target as HTMLInputElement).value;
    renderResults();
  });
  document.getElementById("select-all")?.addEventListener("change", (event) => {
    const checked = (event.target as HTMLInputElement).checked;
    for (const row of filteredRows()) {
      if (checked) state.selected.add(row.symbol);
      else state.selected.delete(row.symbol);
    }
    renderResults();
  });
  document.getElementById("add-selected")?.addEventListener("click", () => {
    void addToWatchlist(selectedSymbols());
  });
  document.getElementById("add-all")?.addEventListener("click", () => {
    void addToWatchlist(state.results.map((row) => row.symbol));
  });
  document.getElementById("new-watchlist")?.addEventListener("click", async () => {
    const name = await textModal("Tạo watchlist", `List ${state.watchlists.length + 1}`);
    if (!name) return;
    try {
      const created = await watchlistApi.create(name);
      state.activeWatchlistId = created.id;
      await loadWatchlists();
      setStatus(`Đã tạo ${created.name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Không tạo được watchlist.", "error");
    }
  });
  document.getElementById("target-watchlist")?.addEventListener("change", (event) => {
    state.activeWatchlistId = Number((event.target as HTMLSelectElement).value);
    renderWatchlists();
    void loadWatchlistItems(state.activeWatchlistId);
  });
  document.getElementById("watchlist-list")?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    const li = target.closest<HTMLLIElement>("li[data-id]");
    if (!li) return;
    const id = Number(li.dataset.id);
    const action = target.dataset.action;
    if (action) {
      const watchlist = state.watchlists.find((item) => item.id === id);
      if (!watchlist) return;
      if (action === "rename") {
        const name = await textModal("Đổi tên watchlist", watchlist.name);
        if (name && name !== watchlist.name) {
          try {
            await watchlistApi.rename(id, name);
            await loadWatchlists();
          } catch (error) {
            setStatus(error instanceof Error ? error.message : "Không đổi tên được.", "error");
          }
        }
      } else if (action === "clear") {
        if (await confirmModal("Làm rỗng watchlist", `Xoá toàn bộ mã trong ${watchlist.name}?`, "Làm rỗng")) {
          await watchlistApi.clear(id);
          await loadWatchlists();
        }
      } else if (action === "delete") {
        if (await confirmModal("Xoá watchlist", `Xoá ${watchlist.name}?`, "Xoá")) {
          try {
            await watchlistApi.remove(id);
            if (state.activeWatchlistId === id) state.activeWatchlistId = null;
            await loadWatchlists();
          } catch (error) {
            setStatus(error instanceof Error ? error.message : "Không xoá được.", "error");
          }
        }
      }
      return;
    }
    state.activeWatchlistId = id;
    renderWatchlists();
    await loadWatchlistItems(id);
  });
  document.getElementById("watchlist-list")?.addEventListener("dblclick", async (event) => {
    const target = event.target as HTMLElement;
    if (target.dataset.role !== "name") return;
    const li = target.closest<HTMLLIElement>("li[data-id]");
    if (!li) return;
    const watchlist = state.watchlists.find((item) => item.id === Number(li.dataset.id));
    if (!watchlist) return;
    const name = await textModal("Đổi tên watchlist", watchlist.name);
    if (name && name !== watchlist.name) {
      try {
        await watchlistApi.rename(watchlist.id, name);
        await loadWatchlists();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Không đổi tên được.", "error");
      }
    }
  });
  document.getElementById("watchlist-items")?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    if (target.dataset.chart) {
      openChart(target.dataset.chart);
      return;
    }
    const symbol = target.dataset.removeSymbol;
    if (symbol && state.activeWatchlistId) {
      await watchlistApi.removeSymbol(state.activeWatchlistId, symbol);
      await loadWatchlists();
    }
  });
  document.getElementById("results-body")?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    if (target.dataset.chart) {
      openChart(target.dataset.chart);
      return;
    }
    const symbol = target.dataset.add;
    if (symbol) await addToWatchlist([symbol]);
  });
  document.getElementById("results-body")?.addEventListener("change", (event) => {
    const input = event.target as HTMLInputElement;
    const symbol = input.dataset.select;
    if (!symbol) return;
    if (input.checked) state.selected.add(symbol);
    else state.selected.delete(symbol);
    renderResults();
  });
  document.querySelector(".results thead")?.addEventListener("click", (event) => {
    const th = (event.target as HTMLElement).closest<HTMLElement>("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort as keyof ScreenerRow;
    if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    else {
      state.sortKey = key;
      state.sortDir = key === "symbol" || key === "status" ? "asc" : "desc";
    }
    renderResults();
  });

  // PRESETS SWITCHING
  document.getElementById("presets-list")?.addEventListener("click", (event) => {
    const li = (event.target as HTMLElement).closest<HTMLLIElement>("li[data-preset-id]");
    if (!li) return;
    const presetId = li.dataset.presetId;
    const preset = PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    state.activePreset = preset.id;

    // highlight active
    document.querySelectorAll(".preset-item").forEach((el) => el.classList.remove("active"));
    li.classList.add("active");

    // update title
    const titleEl = document.getElementById("screener-title");
    if (titleEl) titleEl.textContent = preset.name;

    // update state conditions
    state.conditions = JSON.parse(JSON.stringify(preset.conditions));
    applyConditionsToForm();
    void scan();
  });

  // FILTER CHIP TOGGLE, REMOVE, AND EDIT VIA MODAL
  document.getElementById("filter-chips")?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;

    // 1. Remove chip
    const removeKey = target.dataset.removeChip as keyof ScreenerConditions;
    if (removeKey && state.conditions[removeKey]) {
      state.conditions[removeKey].enabled = false;
      applyConditionsToForm();
      void scan();
      return;
    }

    // 2. Open edit modal
    const editKey = target.dataset.openModal as keyof ScreenerConditions;
    if (editKey) {
      if (editKey === "min_volume") {
        const val = await promptNumberModal("Khối lượng tối thiểu", "Nhập khối lượng khớp lệnh tối thiểu (CP):", state.conditions.min_volume.value, "10000", "CP");
        if (val !== null) {
          state.conditions.min_volume.value = val;
          state.conditions.min_volume.enabled = true;
          applyConditionsToForm();
          void scan();
        }
      } else if (editKey === "min_price") {
        const val = await promptNumberModal("Giá tối thiểu", "Nhập giá tối thiểu (VND):", state.conditions.min_price.value, "500", "VND");
        if (val !== null) {
          state.conditions.min_price.value = val;
          state.conditions.min_price.enabled = true;
          applyConditionsToForm();
          void scan();
        }
      } else if (editKey === "relative_volume") {
        const val = await promptNumberModal("Đột biến khối lượng (Vol / SMA10)", "Tỉ lệ khối lượng hiện tại so với trung bình 10 phiên:", state.conditions.relative_volume.value, "0.1", "lần");
        if (val !== null) {
          state.conditions.relative_volume.value = val;
          state.conditions.relative_volume.enabled = true;
          applyConditionsToForm();
          void scan();
        }
      } else if (editKey === "price_change") {
        const val = await promptNumberModal("Mức tăng giá tối thiểu", "Phần trăm thay đổi giá tối thiểu (%):", state.conditions.price_change.percent, "0.5", "%");
        if (val !== null) {
          state.conditions.price_change.percent = val;
          state.conditions.price_change.enabled = true;
          applyConditionsToForm();
          void scan();
        }
      } else if (editKey === "price_vs_ma5") {
        const val = await promptNumberModal("Vượt đường MA5", "Phần trăm giá hiện tại so với đường MA5 (%):", state.conditions.price_vs_ma5.percent, "0.5", "%");
        if (val !== null) {
          state.conditions.price_vs_ma5.percent = val;
          state.conditions.price_vs_ma5.enabled = true;
          applyConditionsToForm();
          void scan();
        }
      } else if (editKey === "divergence") {
        const val = await divergenceModal(state.conditions.divergence);
        if (val !== null) {
          state.conditions.divergence = { ...val, enabled: true };
          applyConditionsToForm();
          void scan();
        }
      }
      return;
    }

    // 3. Click chip to toggle on/off
    const chipEl = target.closest<HTMLElement>(".filter-chip[data-chip]");
    if (chipEl) {
      const key = chipEl.dataset.chip as keyof ScreenerConditions;
      if (key && state.conditions[key]) {
        state.conditions[key].enabled = !state.conditions[key].enabled;
        applyConditionsToForm();
        void scan();
      }
    }
  });
}

export function mountScreener(root: HTMLElement): void {
  document.body.classList.add("screener-page");
  root.innerHTML = shell();
  applyConditionsToForm();
  bindEvents();
  void loadWatchlists();
  void scan();
}
