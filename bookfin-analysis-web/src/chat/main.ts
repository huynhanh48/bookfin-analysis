import { marked } from "marked";

import {
  analysisCaseApi,
  chatApi,
  knowledgeApi,
  type AnalysisCaseRecord,
  type AnalysisJob,
  type AssessmentStatus,
  type ChatMessage,
  type ChatTurn,
  type KnowledgeDocument,
  type ResearchReport,
} from "./api";
import "./styles.css";

const STORAGE_KEY = "bookfin_chat_history_v3";

const DEFAULT_MESSAGES: ChatMessage[] = [
  {
    id: "msg-1",
    role: "assistant",
    text: "Xin chào! Tôi là BookFin AI. Bạn có thể hỏi về diễn biến thị trường, phân tích kỹ thuật (RSI, MACD, MA, nến) hoặc các tài liệu, ghi chú đã nạp vào hệ thống.",
    timestamp: "09:00",
  },
  {
    id: "msg-2",
    role: "user",
    text: "Tình hình kỹ thuật cổ phiếu SSI hiện tại như thế nào?",
    timestamp: "09:01",
  },
  {
    id: "msg-3",
    role: "assistant",
    text: "Theo dữ liệu thị trường và phân tích kỹ thuật mới nhất:\n\n1. Xu hướng giá: SSI đang giao dịch tích luỹ quanh vùng 20.000 VND trên đường MA5.\n2. Khối lượng: Thanh khoản ổn định đạt hơn 25 triệu cổ phiếu.\n3. Chỉ báo động lượng: RSI dao động quanh mức 53.5, MACD đang ở trạng thái trung tính chờ bứt phá.\n4. Phân kỳ: Không phát hiện phân kỳ giảm lớn ở khung ngày.",
    timestamp: "09:02",
  },
];

const PRESET_TOPICS = [
  { id: "top-1", title: "Phân tích kỹ thuật SSI & MACD" },
  { id: "top-2", title: "Bộ lọc cổ phiếu đột biến Vol" },
  { id: "top-3", title: "Chiến lược giao dịch Ichimoku" },
  { id: "top-4", title: "Phân kỳ thường vs Phân kỳ ẩn" },
  { id: "top-5", title: "Tổng quan thị trường VN-Index" },
  { id: "top-6", title: "Kịch bản sóng Elliott VN30" },
  { id: "top-7", title: "Phân tích BCTC Hoà Phát Q3" },
];

function formatTime(): string {
  const now = new Date();
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

export function mountChat(root: HTMLDivElement): void {
  document.title = "BookFin AI · Research & Knowledge RAG";

  let messages: ChatMessage[] = [...DEFAULT_MESSAGES];
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) messages = JSON.parse(saved);
  } catch {
    // fallback
  }

  let ragDocuments: KnowledgeDocument[] = [];
  let isSending = false;

  root.innerHTML = `
    <div class="chat-app" id="chat-app-root">
      <!-- 1. Left Icon Strip -->
      <aside class="chat-icon-strip">
        <button type="button" class="chat-logo-badge" id="btn-toggle-sidebar" title="Thu gọn / Mở rộng sidebar">B</button>
        <nav class="chat-icon-nav">
          <a href="/" class="chat-nav-btn" title="Biểu đồ KLineChart">
            <svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
          </a>
          <a href="/screener" class="chat-nav-btn" title="Bộ lọc cổ phiếu (Filter Money)">
            <svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z"/></svg>
          </a>
          <button type="button" class="chat-nav-btn active" id="nav-btn-chat" title="Chatbot AI">
            <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/></svg>
          </button>
          <button type="button" class="chat-nav-btn" id="nav-btn-rag" title="Kho kiến thức & Quản lý RAG">
            <svg viewBox="0 0 24 24"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H8V4h12v12z"/></svg>
          </button>
        </nav>

        <div class="chat-strip-bottom">
          <div class="chat-avatar-text" title="BookFin User">BF</div>
        </div>
      </aside>

      <!-- VIEW 1: CHAT VIEW (Subsidebar + Main Chat Window) -->
      <div id="view-chat-container" style="display:flex; flex:1; height:100%; min-width:0;">
        <!-- 2. Sub-Sidebar (Conversations list) -->
        <aside class="chat-subsidebar">
          <div class="chat-subsidebar-head">
            <div class="chat-app-title">
              <span class="bot-name">BookFin AI</span>
            </div>
            <button type="button" class="chat-new-btn" id="btn-new-chat" title="Hội thoại mới">
              <span>+ Mới</span>
            </button>
          </div>

          <div class="chat-history-scroll">
            <div class="chat-group-title">
              <span>Lịch sử hội thoại</span>
            </div>
            <div id="chat-history-list"></div>
            <div id="chat-history-error"></div>

            <div class="chat-group-title" style="margin-top:16px;">
              <span>Chủ đề / Dữ liệu RAG</span>
            </div>
            <div id="chat-topics-list"></div>
          </div>
        </aside>

        <!-- 3. Central Chat Window (Full remaining width, Clean & Unified) -->
        <main class="chat-main-column">
          <header class="chat-main-header">
            <div class="chat-header-title">
              <h3>BookFin Intelligence</h3>
              <span class="chat-header-badge">RAG Active</span>
            </div>

            <div class="chat-header-actions">
              <button type="button" class="chat-header-btn" id="btn-switch-to-rag">
                <svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H8V4h12v12z"/></svg>
                <span>Kho kiến thức RAG</span>
              </button>
            </div>
          </header>

          <!-- Message List -->
          <div class="chat-messages-container" id="chat-thread"></div>

          <!-- Floating Input Container -->
          <div class="chat-composer-container">
            <div id="chat-pasted-container"></div>
            <form id="chat-input-form" class="chat-pill-input-box">
              <div class="chat-attach-wrap" id="chat-attach-wrap">
                <button type="button" class="chat-attach-btn" id="btn-attach" title="Thêm tệp, ảnh hoặc công cụ" aria-expanded="false" aria-haspopup="menu">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                </button>
                <div class="chat-attach-menu" id="chat-attach-menu" role="menu" aria-hidden="true">
                  <button type="button" class="chat-attach-menu-item" data-attach-action="upload" role="menuitem">
                    <span class="chat-attach-menu-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                    </span>
                    <span class="chat-attach-menu-text">
                      <strong>Thêm ảnh &amp; tệp</strong>
                      <small>Tải lên ảnh biểu đồ hoặc PDF từ máy tính</small>
                    </span>
                  </button>
                  <button type="button" class="chat-attach-menu-item" data-attach-action="mode-knowledge" role="menuitem">
                    <span class="chat-attach-menu-icon blue">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                    </span>
                    <span class="chat-attach-menu-text">
                      <strong>Kho kiến thức RAG</strong>
                      <small>Hỏi đáp dựa trên tài liệu đã nạp</small>
                    </span>
                  </button>
                  <button type="button" class="chat-attach-menu-item" data-attach-action="mode-research" role="menuitem">
                    <span class="chat-attach-menu-icon emerald">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>
                    </span>
                    <span class="chat-attach-menu-text">
                      <strong>Phân tích cổ phiếu</strong>
                      <small>Nghiên cứu kỹ thuật &amp; dữ liệu thị trường</small>
                    </span>
                  </button>
                  <div class="chat-attach-menu-divider"></div>
                  <button type="button" class="chat-attach-menu-item" data-attach-action="open-rag" role="menuitem">
                    <span class="chat-attach-menu-icon slate">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                    </span>
                    <span class="chat-attach-menu-text">
                      <strong>Quản lý kho tài liệu</strong>
                      <small>Mở trang nạp và quản lý dữ liệu RAG</small>
                    </span>
                  </button>
                </div>
              </div>
              <input type="file" id="chat-hidden-file" hidden accept="image/png,image/jpeg,image/webp,application/pdf" />

              <div id="chat-mode-tag-container" class="chat-mode-tag-container"></div>
              <textarea id="chat-textarea" class="chat-input-field" rows="1" placeholder="Hỏi AI..."></textarea>

              <button type="submit" class="chat-send-btn" id="btn-send" title="Gửi tin nhắn">
                <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
              </button>
            </form>
            <div class="chat-disclaimer">
              BookFin AI có thể đưa ra nhận định tham khảo. Luôn kiểm chứng lại dữ liệu tài chính trước khi giao dịch.
            </div>
          </div>
        </main>
      </div>

      <!-- VIEW 2: DEDICATED RAG KNOWLEDGE CONTAINER (App riêng biệt) -->
      <div id="view-rag-container" class="rag-container-view" style="display:none;">
        <header class="rag-header">
          <div class="rag-header-left">
            <h2>Quản lý Kho Kiến Thức (Knowledge RAG)</h2>
            <span class="rag-stats-pill" id="rag-doc-count">0 tài liệu</span>
          </div>

          <div class="chat-header-actions">
            <button type="button" class="chat-header-btn" id="btn-refresh-rag">
              <span>Làm mới ↻</span>
            </button>
            <button type="button" class="chat-header-btn" id="btn-back-to-chat" style="background:var(--chat-primary);color:#fff;border-color:var(--chat-primary);">
              <span>← Trở về Chat AI</span>
            </button>
          </div>
        </header>

        <div class="rag-tabs">
          <button type="button" class="rag-tab active" id="rag-tab-knowledge" data-rag-tab="knowledge">
            Tài liệu Knowledge
          </button>
          <button type="button" class="rag-tab" id="rag-tab-cases" data-rag-tab="cases">
            Case phân tích
          </button>
        </div>

        <div class="rag-body-grid rag-panel active" id="rag-knowledge-panel">
          <!-- Form Panel: Nạp tài liệu mới -->
          <div class="rag-card">
            <div class="rag-card-head">
              <h4>Nạp tài liệu mới vào RAG</h4>
            </div>
            <div class="rag-card-body">
              <form id="rag-upload-form">
                <div class="rag-form-group">
                  <label>Loại nội dung</label>
                  <select id="rag-kind" class="rag-input">
                    <option value="text">Văn bản / Ghi chú phân tích</option>
                    <option value="image">Ảnh biểu đồ TradingView / KLineChart</option>
                    <option value="document">Tài liệu PDF / Báo cáo tài chính</option>
                  </select>
                </div>

                <div class="rag-form-group">
                  <label>Tên nguồn tài liệu</label>
                  <input id="rag-source" class="rag-input" required placeholder="Ví dụ: BCTC Q3 SSI, Ghi chú phân tích HPG..." />
                </div>

                <div class="rag-form-group" id="rag-text-group">
                  <label>Nội dung văn bản</label>
                  <textarea id="rag-text" class="rag-input" rows="6" placeholder="Nhập nội dung nhận định, tóm tắt hoặc luận điểm đầu tư..."></textarea>
                </div>

                <div class="rag-form-group" id="rag-file-group" style="display:none;">
                  <label>Tệp đính kèm (Ảnh hoặc PDF)</label>
                  <input type="file" id="rag-file" class="rag-input" />
                </div>

                <button type="submit" class="rag-btn-primary" id="rag-submit-btn">Nạp vào Knowledge RAG</button>
              </form>
              <div id="rag-upload-status" style="font-size:12px;color:var(--chat-text-muted);margin-top:10px;line-height:1.4;"></div>
            </div>
          </div>

          <!-- Table Panel: Danh sách tài liệu đã index -->
          <div class="rag-card">
            <div class="rag-card-head">
              <h4>Tài liệu & Chỉ mục trong Database</h4>
            </div>
            <div class="rag-table-wrap">
              <table class="rag-table">
                <thead>
                  <tr>
                    <th>Nguồn tài liệu</th>
                    <th>Loại</th>
                    <th>Trạng thái</th>
                    <th>Tiến độ</th>
                    <th>Ngày tạo</th>
                    <th style="width:130px;text-align:right;">Thao tác</th>
                  </tr>
                </thead>
                <tbody id="rag-table-body">
                  <tr>
                    <td colspan="6" style="text-align:center;padding:30px;color:var(--chat-text-muted);">Đang tải danh sách tài liệu...</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="rag-body-grid rag-panel" id="rag-case-panel">
          <!-- Form Panel: Nạp Case phân tích -->
          <div class="rag-card">
            <div class="rag-card-head">
              <h4>Nạp Case phân tích mới</h4>
            </div>
            <div class="rag-card-body">
              <form id="case-upload-form">
                <div class="rag-form-row">
                  <div class="rag-form-group">
                    <label>Mã cổ phiếu *</label>
                    <input id="case-symbol" class="rag-input" required maxlength="20" placeholder="Ví dụ: SSI" />
                  </div>
                  <div class="rag-form-group">
                    <label>Khung thời gian</label>
                    <select id="case-timeframe" class="rag-input">
                      <option value="D1">D1</option>
                      <option value="W1">W1</option>
                      <option value="M1">M1</option>
                      <option value="H1">H1</option>
                    </select>
                  </div>
                </div>

                <div class="rag-form-group">
                  <label>Thời điểm phân tích (as-of)</label>
                  <input id="case-analyzed-at" type="datetime-local" class="rag-input" />
                  <div class="rag-form-hint">Bỏ trống nếu phân tích ở hiện tại. Case lịch sử phải dùng đúng thời điểm đã biết.</div>
                </div>

                <div class="rag-form-group">
                  <label>Luận điểm / ghi chú phân tích *</label>
                  <textarea id="case-thesis" class="rag-input" rows="6" required placeholder="Nhập luận điểm đầu tư, bối cảnh thị trường và kết luận..."></textarea>
                </div>

                <div class="rag-form-row">
                  <div class="rag-form-group">
                    <label>Giá cơ sở</label>
                    <select id="case-price-basis" class="rag-input">
                      <option value="RAW">RAW (khuyến nghị)</option>
                      <option value="ADJUSTED">ADJUSTED</option>
                    </select>
                  </div>
                  <div class="rag-form-group">
                    <label>Nguồn</label>
                    <input id="case-source" class="rag-input" value="user" maxlength="1000" />
                  </div>
                </div>

                <div class="rag-form-group">
                  <label>Ảnh chart TradingView (tuỳ chọn)</label>
                  <input id="case-chart" type="file" class="rag-input" accept="image/png,image/jpeg,image/webp" />
                </div>

                <button type="submit" class="rag-btn-primary" id="case-submit-btn">Nạp Case vào Analysis Memory</button>
              </form>
              <div id="case-upload-status" class="rag-form-hint" style="margin-top:10px;"></div>
            </div>
          </div>

          <!-- Case detail / lookup Panel -->
          <div class="rag-card">
            <div class="rag-card-head">
              <h4>Case đã tạo / Tra cứu</h4>
            </div>
            <div class="rag-card-body">
              <div class="rag-lookup-row">
                <input id="case-lookup-id" class="rag-input" placeholder="Dán UUID case để mở..." />
                <button type="button" class="rag-btn-primary rag-btn-inline" id="case-lookup-btn">Mở case</button>
              </div>
              <div class="rag-case-note">
                Case lưu vào không gian riêng (<code>cases</code>) và có thể được agent dùng làm bằng chứng lịch sử.
                Backend chưa có API liệt kê case; dùng ID trả về sau khi nạp để tra cứu.
              </div>
              <div id="case-detail">
                <div class="rag-form-hint">Nạp case mới hoặc mở case theo ID để xem trạng thái.</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Detail Modal -->
      <dialog id="chat-doc-modal" class="chat-detail">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;border-bottom:1px solid var(--chat-border);padding-bottom:10px;">
          <h3 id="modal-doc-title" style="margin:0;font-size:15px;font-weight:700;">Chi tiết tài liệu</h3>
          <button type="button" id="modal-doc-close" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--chat-text-muted);">×</button>
        </div>
        <p id="modal-doc-meta" style="font-size:12px;color:var(--chat-text-muted);margin-bottom:10px;"></p>
        <pre id="modal-doc-text" style="font-family:inherit;font-size:12.5px;white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:6px;border:1px solid var(--chat-border);max-height:300px;overflow:auto;"></pre>
        <div id="modal-doc-file" style="margin-top:12px;"></div>
      </dialog>
    </div>
  `;

  const appRoot = root.querySelector<HTMLDivElement>("#chat-app-root")!;
  const btnToggleSidebar = root.querySelector<HTMLButtonElement>("#btn-toggle-sidebar");
  btnToggleSidebar?.addEventListener("click", () => {
    appRoot.classList.toggle("sidebar-collapsed");
  });

  const chatView = root.querySelector<HTMLElement>("#view-chat-container")!;
  const ragView = root.querySelector<HTMLElement>("#view-rag-container")!;
  const navBtnChat = root.querySelector<HTMLButtonElement>("#nav-btn-chat")!;
  const navBtnRag = root.querySelector<HTMLButtonElement>("#nav-btn-rag")!;

  const thread = root.querySelector<HTMLDivElement>("#chat-thread")!;
  const textarea = root.querySelector<HTMLTextAreaElement>("#chat-textarea")!;
  const inputForm = root.querySelector<HTMLFormElement>("#chat-input-form")!;
  const hiddenFileInput = root.querySelector<HTMLInputElement>("#chat-hidden-file")!;
  const topicsList = root.querySelector<HTMLDivElement>("#chat-topics-list")!;
  const chatList = root.querySelector<HTMLDivElement>("#chat-history-list")!;
  const historyError = root.querySelector<HTMLDivElement>("#chat-history-error")!;
  const modeTagContainer = root.querySelector<HTMLDivElement>("#chat-mode-tag-container")!;
  let selectedMode: "knowledge" | "research" = "knowledge";
  let showModeTag = false;

  function renderModeTag(): void {
    if (!showModeTag) {
      modeTagContainer.innerHTML = "";
      return;
    }
    const isResearch = selectedMode === "research";
    const iconSvg = isResearch
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`;
    const label = isResearch ? "Phân tích cổ phiếu" : "Kho kiến thức RAG";
    modeTagContainer.innerHTML = `
      <span class="chat-mode-tag ${isResearch ? "research" : "knowledge"}">
        ${iconSvg}
        <span>${label}</span>
        <button type="button" class="chat-mode-tag-clear" id="btn-clear-mode-tag" title="Xoá chế độ">×</button>
      </span>
    `;
    modeTagContainer.querySelector("#btn-clear-mode-tag")?.addEventListener("click", () => {
      selectedMode = "knowledge";
      showModeTag = false;
      renderModeTag();
      textarea.focus();
    });
  }

  const ACTIVE_CHAT_KEY = "bookfin_active_chat_id";
  let activeChat: string | null = localStorage.getItem(ACTIVE_CHAT_KEY);

  function setActive(id: string | null): void {
    activeChat = id;
    if (id) localStorage.setItem(ACTIVE_CHAT_KEY, id);
    else localStorage.removeItem(ACTIVE_CHAT_KEY);
  }

  async function operate(action: () => Promise<void>): Promise<void> {
    try {
      if (historyError) historyError.textContent = "";
      await action();
    } catch (err) {
      if (historyError) historyError.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  function setView(view: "chat" | "rag"): void {
    if (view === "chat") {
      chatView.style.display = "flex";
      ragView.style.display = "none";
      navBtnChat.classList.add("active");
      navBtnRag.classList.remove("active");
    } else {
      chatView.style.display = "none";
      ragView.style.display = "flex";
      navBtnChat.classList.remove("active");
      navBtnRag.classList.add("active");
      void loadRagDocuments();
    }
  }

  navBtnChat.addEventListener("click", () => setView("chat"));
  navBtnRag.addEventListener("click", () => setView("rag"));
  root.querySelector("#btn-switch-to-rag")?.addEventListener("click", () => setView("rag"));
  root.querySelector("#btn-back-to-chat")?.addEventListener("click", () => setView("chat"));

  function saveMessages(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // quota
    }
  }

  function turnMessages(turn: ChatTurn): ChatMessage[] {
    const timestamp = new Date(turn.created_at).toLocaleTimeString("vi-VN", {
      hour: "2-digit", minute: "2-digit",
    });
    return [
      { id: `user-${turn.position}`, role: "user", text: turn.question, timestamp },
      { id: `assistant-${turn.position}`, role: "assistant", text: turn.answer, timestamp, sources: turn.sources, report: turn.report ?? undefined },
    ];
  }

  async function openChat(id: string): Promise<void> {
    const detail = await chatApi.detail(id);
    setActive(id);
    messages = detail.turns.flatMap(turnMessages);
    textarea.value = "";
    renderMessages();
  }

  async function renderHistory(restore = false): Promise<void> {
    const chats = await chatApi.list();
    if (restore) {
      const saved = localStorage.getItem(ACTIVE_CHAT_KEY);
      if (saved && chats.some((chat) => chat.id === saved)) await openChat(saved);
    }
    chatList.replaceChildren();
    for (const chat of chats) {
      const row = document.createElement("div");
      row.className = "chat-conversation-row";
      const open = document.createElement("button");
      open.type = "button";
      open.className = `chat-history-item${chat.id === activeChat ? " active" : ""}`;
      open.textContent = chat.title;
      open.title = chat.title;
      open.onclick = () => void operate(async () => {
        await openChat(chat.id);
        for (const selected of chatList.querySelectorAll(".active")) selected.classList.remove("active");
        open.classList.add("active");
      });
      const actions = document.createElement("details");
      actions.className = "chat-conversation-actions";
      actions.innerHTML = '<summary aria-label="Tùy chọn hội thoại">⋯</summary><div><button type="button">Đổi tên</button><button type="button">Xoá</button></div>';
      const [rename, remove] = actions.querySelectorAll("button");

      // Inline edit row: thay thế window.prompt bằng input ngay trên dòng
      rename.onclick = () => {
        actions.open = false;
        row.replaceChildren();

        const form = document.createElement("form");
        form.className = "chat-rename-form";

        const input = document.createElement("input");
        input.type = "text";
        input.className = "chat-rename-input";
        input.value = chat.title;
        input.maxLength = 100;

        const saveBtn = document.createElement("button");
        saveBtn.type = "submit";
        saveBtn.className = "chat-rename-save-btn";
        saveBtn.textContent = "Lưu";

        const cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "chat-rename-cancel-btn";
        cancelBtn.textContent = "Huỷ";

        cancelBtn.onclick = () => {
          void renderHistory();
        };

        form.onsubmit = (e) => {
          e.preventDefault();
          const newTitle = input.value.trim();
          if (!newTitle || newTitle === chat.title) {
            void renderHistory();
            return;
          }
          void operate(async () => {
            await chatApi.rename(chat.id, newTitle);
            await renderHistory();
          });
        };

        form.append(input, saveBtn, cancelBtn);
        row.append(form);
        input.focus();
        input.select();
      };

      remove.onclick = () => void operate(async () => {
        actions.open = false;
        if (!window.confirm(`Xoá hội thoại “${chat.title}” và toàn bộ tin nhắn?`)) return;
        await chatApi.remove(chat.id);
        if (chat.id === activeChat) {
          setActive(null);
          messages = [];
          renderMessages();
        }
        await renderHistory();
      });
      row.append(open, actions);
      chatList.append(row);
    }
  }

  const pastedContainer = root.querySelector<HTMLDivElement>("#chat-pasted-container")!;
  let attachedFile: File | null = null;
  let attachedPreviewUrl: string | null = null;

  function renderPastedPreview(): void {
    if (!attachedFile || !attachedPreviewUrl) {
      pastedContainer.innerHTML = "";
      return;
    }
    pastedContainer.innerHTML = `
      <div class="chat-pasted-preview">
        <img src="${attachedPreviewUrl}" alt="Preview" />
        <div class="chat-pasted-info">
          <span>${escapeHtml(attachedFile.name)}</span>
          <small style="color:var(--chat-text-muted);font-size:11px;">${(attachedFile.size / 1024).toFixed(1)} KB</small>
        </div>
        <button type="button" class="chat-pasted-remove" id="btn-remove-pasted" title="Gỡ bỏ ảnh">×</button>
      </div>
    `;
    pastedContainer.querySelector("#btn-remove-pasted")?.addEventListener("click", () => {
      clearPastedFile();
    });
  }

  function clearPastedFile(): void {
    if (attachedPreviewUrl) URL.revokeObjectURL(attachedPreviewUrl);
    attachedFile = null;
    attachedPreviewUrl = null;
    renderPastedPreview();
  }

  function attachSelectedFile(file: File): void {
    if (attachedPreviewUrl) URL.revokeObjectURL(attachedPreviewUrl);
    attachedFile = file;
    attachedPreviewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
    renderPastedPreview();
    textarea.focus();
  }

  // Handle Ctrl+V / Command+V paste images like ChatGPT
  window.addEventListener("paste", (event: ClipboardEvent) => {
    const items = event.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.indexOf("image") !== -1) {
        const file = item.getAsFile();
        if (file) {
          event.preventDefault();
          attachSelectedFile(file);
          break;
        }
      }
    }
  });

  // Handle Drag & Drop images/files onto the chat without opening a new browser tab
  let dragDepth = 0;
  window.addEventListener("dragenter", (event: DragEvent) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    dragDepth += 1;
    inputForm.classList.add("drag-over");
  });

  window.addEventListener("dragover", (event: DragEvent) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    inputForm.classList.add("drag-over");
  });

  window.addEventListener("dragleave", (event: DragEvent) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
      inputForm.classList.remove("drag-over");
    }
  });

  window.addEventListener("drop", (event: DragEvent) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    dragDepth = 0;
    inputForm.classList.remove("drag-over");
    const file = event.dataTransfer.files?.[0];
    if (file) {
      attachSelectedFile(file);
    }
  });

  function renderMessages(): void {
    thread.innerHTML = messages
      .map((msg) => {
        const isUser = msg.role === "user";
        const isTyping = msg.id.startsWith("typing-");
        const safeText = String(msg.text ?? "").trim();
        let renderedBody = "";
        if (isTyping) {
          renderedBody = `<div class="chat-typing-dots"><span></span><span></span><span></span></div>`;
        } else if (isUser) {
          renderedBody = `<span class="chat-user-text">${escapeHtml(safeText)}</span>`;
        } else if (msg.report) {
          renderedBody = renderResearchReport(msg.report);
        } else {
          try {
            renderedBody = String(marked.parse(safeText));
          } catch {
            renderedBody = escapeHtml(safeText);
          }
        }
        return `
          <div class="chat-message-row ${isUser ? "user" : "bot"}">
            <div class="chat-avatar-circle ${isUser ? "user" : "bot"}">
              ${isUser ? "Tôi" : "AI"}
            </div>
            <div class="chat-message-content">
              <div class="chat-meta-info">
                <strong>${isUser ? "Bạn" : "BookFin AI"}</strong>
                <span>${msg.timestamp || ""}</span>
              </div>
              
              <div class="chat-bubble ${!isUser && !isTyping && !msg.report ? "markdown-body" : ""}${msg.report ? " analysis-bubble" : ""}">${renderedBody}${msg.imageUrl
            ? `<div style="margin-top:8px;"><img src="${msg.imageUrl}" class="chat-attached-image" style="max-width:320px;max-height:240px;border-radius:6px;display:block;border:1px solid #cbd5e1;cursor:pointer;" alt="Ảnh đính kèm" onclick="window.open(this.src)" /></div>`
            : ""
          }${msg.sources && msg.sources.length > 0
            ? `<div class="chat-sources-box"><strong>Nguồn tham chiếu:</strong> ${msg.sources.map((s) => String(s.source || s.title || "Tài liệu RAG")).join(", ")}</div>`
            : ""
          }</div>

              ${!isUser && !isTyping
            ? `
                <div class="chat-msg-actions">
                  <button type="button" class="chat-msg-action-btn" title="Sao chép nội dung" data-copy="${escapeHtml(safeText)}">
                    Sao chép
                  </button>
                </div>
              `
            : ""
          }
            </div>
          </div>
        `;
      })
      .join("");

    thread.scrollTop = thread.scrollHeight;

    thread.querySelectorAll<HTMLButtonElement>("[data-copy]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const textToCopy = btn.dataset.copy || "";
        navigator.clipboard.writeText(textToCopy);
        btn.textContent = "Đã chép ✓";
        setTimeout(() => (btn.textContent = "Sao chép"), 1500);
      });
    });
  }

  async function loadTopicsFromBackend(): Promise<void> {
    if (!topicsList) return;
    try {
      const res = await knowledgeApi.list();
      const docs = res.data || [];
      if (docs.length === 0) {
        topicsList.innerHTML = PRESET_TOPICS.map(
          (p) => `<div class="chat-history-item" data-topic="${p.title}"><span class="title">${p.title}</span></div>`,
        ).join("");
      } else {
        topicsList.innerHTML = docs.slice(0, 8).map(
          (doc) => `<div class="chat-history-item" data-topic="Hỏi về tài liệu ${doc.source}" title="${doc.source} (${doc.kind.toUpperCase()})"><span class="title">📄 ${escapeHtml(doc.source)}</span></div>`,
        ).join("");
      }

      topicsList.querySelectorAll<HTMLElement>(".chat-history-item[data-topic]").forEach((item) => {
        item.addEventListener("click", () => {
          const topic = item.dataset.topic;
          if (topic) {
            textarea.value = `Hãy phân tích chi tiết: ${topic}`;
            textarea.focus();
          }
        });
      });
    } catch {
      topicsList.innerHTML = PRESET_TOPICS.map(
        (p) => `<div class="chat-history-item" data-topic="${p.title}"><span class="title">${p.title}</span></div>`,
      ).join("");
    }
  }

  renderMessages();
  void renderHistory(true);
  void loadTopicsFromBackend();

  async function handleSend(): Promise<void> {
    const text = textarea.value.trim();
    if (!text || isSending) return;

    const currentImgUrl = attachedPreviewUrl;
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: "user",
      text,
      timestamp: formatTime(),
      imageUrl: currentImgUrl || undefined,
    };
    messages.push(userMsg);
    textarea.value = "";
    textarea.style.height = "auto";
    clearPastedFile();
    renderMessages();
    saveMessages();

    isSending = true;

    const typingMsg: ChatMessage = {
      id: `typing-${Date.now()}`,
      role: "assistant",
      text: "Đang suy nghĩ...",
      timestamp: formatTime(),
    };
    messages.push(typingMsg);
    renderMessages();

    const mode = selectedMode;

    try {
      if (!activeChat) {
        const created = await chatApi.create();
        setActive(created.id);
        void renderHistory();
      }

      if (activeChat) {
        const turn = await chatApi.send(activeChat, text, mode);
        messages = messages.filter((m) => m.id !== typingMsg.id);
        messages.push({
          id: `asst-${Date.now()}`,
          role: "assistant",
          text: turn.answer,
          timestamp: formatTime(),
          sources: turn.sources,
          report: turn.report ?? undefined,
        });
        void renderHistory();
      } else {
        throw new Error("Không thể tạo phiên hội thoại.");
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      messages = messages.filter((m) => m.id !== typingMsg.id);
      if (mode === "knowledge") {
        // Read-only fallback when the transcript API is unavailable. Research
        // mode must never silently downgrade to RAG: it would show a knowledge
        // answer while the user asked for a technical analysis.
        try {
          const res = await knowledgeApi.ask(text, "hybrid");
          messages.push({
            id: `asst-${Date.now()}`,
            role: "assistant",
            text: res.data?.answer || "Không tìm thấy dữ liệu phù hợp.",
            timestamp: formatTime(),
            sources: res.data?.sources,
          });
        } catch {
          messages.push({
            id: `asst-${Date.now()}`,
            role: "assistant",
            text: `Không thể xử lý câu hỏi: ${detail}`,
            timestamp: formatTime(),
          });
        }
      } else {
        const hint = /agent disabled/i.test(detail)
          ? "\n\nChế độ Phân tích cổ phiếu đang tắt (`AGENT_ENABLED=false`). Bật trong `.env` rồi restart API."
          : "";
        messages.push({
          id: `asst-${Date.now()}`,
          role: "assistant",
          text: `Phân tích cổ phiếu thất bại: ${detail}${hint}`,
          timestamp: formatTime(),
        });
      }
    } finally {
      isSending = false;
      renderMessages();
      saveMessages();
    }
  }

  inputForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void handleSend();
  });

  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  });

  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 100)}px`;
  });

  root.querySelector("#btn-new-chat")?.addEventListener("click", () => void operate(async () => {
    const created = await chatApi.create();
    setActive(created.id);
    messages = [
      {
        id: `welcome-${Date.now()}`,
        role: "assistant",
        text: "Hội thoại mới đã bắt đầu! Bạn muốn phân tích mã cổ phiếu hay hỏi về tài liệu trong kho kiến thức?",
        timestamp: formatTime(),
      },
    ];
    textarea.value = "";
    clearPastedFile();
    renderMessages();
    saveMessages();
    await renderHistory();
  }));

  root.querySelectorAll<HTMLElement>(".chat-history-item[data-topic]").forEach((item) => {
    item.addEventListener("click", () => {
      const topic = item.dataset.topic;
      if (topic) {
        textarea.value = `Hãy phân tích chi tiết: ${topic}`;
        textarea.focus();
      }
    });
  });

  const attachWrap = root.querySelector<HTMLDivElement>("#chat-attach-wrap");
  const attachBtn = root.querySelector<HTMLButtonElement>("#btn-attach");
  const attachMenu = root.querySelector<HTMLDivElement>("#chat-attach-menu");

  function setAttachMenuOpen(open: boolean): void {
    if (!attachMenu || !attachBtn) return;
    attachMenu.classList.toggle("open", open);
    attachMenu.setAttribute("aria-hidden", String(!open));
    attachBtn.setAttribute("aria-expanded", String(open));
    attachBtn.classList.toggle("active", open);
  }

  attachBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    setAttachMenuOpen(!attachMenu?.classList.contains("open"));
  });

  attachMenu?.querySelectorAll<HTMLButtonElement>("[data-attach-action]").forEach((item) => {
    item.addEventListener("click", () => {
      const action = item.dataset.attachAction;
      setAttachMenuOpen(false);
      if (action === "upload") {
        hiddenFileInput.click();
      } else if (action === "mode-knowledge") {
        selectedMode = "knowledge";
        showModeTag = true;
        renderModeTag();
        textarea.focus();
      } else if (action === "mode-research") {
        selectedMode = "research";
        showModeTag = true;
        renderModeTag();
        textarea.focus();
      } else if (action === "open-rag") {
        setView("rag");
      }
    });
  });

  document.addEventListener("click", (e) => {
    if (attachWrap && !attachWrap.contains(e.target as Node)) {
      setAttachMenuOpen(false);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setAttachMenuOpen(false);
  });

  hiddenFileInput.addEventListener("change", () => {
    const file = hiddenFileInput.files?.[0];
    if (file) {
      if (attachedPreviewUrl) URL.revokeObjectURL(attachedPreviewUrl);
      attachedFile = file;
      attachedPreviewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
      renderPastedPreview();
      textarea.focus();
      hiddenFileInput.value = "";
    }
  });

  // =========================================================================
  // DEDICATED RAG KNOWLEDGE LOGIC
  // =========================================================================
  const ragTableBody = root.querySelector<HTMLTableSectionElement>("#rag-table-body")!;
  const ragDocCount = root.querySelector<HTMLElement>("#rag-doc-count")!;
  const ragUploadForm = root.querySelector<HTMLFormElement>("#rag-upload-form")!;
  const ragKind = root.querySelector<HTMLSelectElement>("#rag-kind")!;
  const ragSource = root.querySelector<HTMLInputElement>("#rag-source")!;
  const ragText = root.querySelector<HTMLTextAreaElement>("#rag-text")!;
  const ragFile = root.querySelector<HTMLInputElement>("#rag-file")!;
  const ragStatus = root.querySelector<HTMLElement>("#rag-upload-status")!;
  const ragTextGroup = root.querySelector<HTMLElement>("#rag-text-group")!;
  const ragFileGroup = root.querySelector<HTMLElement>("#rag-file-group")!;

  ragKind.addEventListener("change", () => {
    const isText = ragKind.value === "text";
    ragTextGroup.style.display = isText ? "block" : "none";
    ragFileGroup.style.display = isText ? "none" : "block";
    ragFile.accept = ragKind.value === "image" ? "image/png,image/jpeg,image/webp" : "application/pdf";
  });

  async function loadRagDocuments(): Promise<void> {
    ragTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--chat-text-muted);">Đang tải danh sách tài liệu...</td></tr>`;
    try {
      const res = await knowledgeApi.list();
      ragDocuments = res.data || [];
      ragDocCount.textContent = `${ragDocuments.length} tài liệu`;

      if (ragDocuments.length === 0) {
        ragTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--chat-text-muted);">Chưa có tài liệu nào. Hãy sử dụng form bên trái để nạp tài liệu vào hệ thống.</td></tr>`;
        return;
      }

      ragTableBody.innerHTML = ragDocuments
        .map((doc) => {
          const statusClass = doc.status.toLowerCase();
          return `
          <tr>
            <td style="font-weight:600;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
              ${escapeHtml(doc.source)}
            </td>
            <td><span class="rag-badge">${doc.kind.toUpperCase()}</span></td>
            <td><span class="rag-badge ${statusClass}">${doc.status}</span></td>
            <td>${doc.progress}%</td>
            <td style="color:var(--chat-text-muted);font-size:11.5px;">${new Date(doc.created_at).toLocaleString("vi-VN")}</td>
            <td style="text-align:right;">
              <button type="button" class="rag-action-btn" data-view-doc="${doc.id}">Xem</button>
              ${["FAILED", "PARTIAL", "REMOVED"].includes(doc.status)
              ? `<button type="button" class="rag-action-btn" data-retry-doc="${doc.id}">Thử lại</button>`
              : ""
            }
              <button type="button" class="rag-action-btn danger" data-delete-doc="${doc.id}">Gỡ</button>
            </td>
          </tr>
        `;
        })
        .join("");

      // Bind actions
      ragTableBody.querySelectorAll<HTMLButtonElement>("[data-view-doc]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.viewDoc;
          if (id) void showDocDetail(id);
        });
      });

      ragTableBody.querySelectorAll<HTMLButtonElement>("[data-retry-doc]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.retryDoc;
          if (id) {
            try {
              await knowledgeApi.retry(id);
              await loadRagDocuments();
            } catch (e) {
              alert(String(e));
            }
          }
        });
      });

      ragTableBody.querySelectorAll<HTMLButtonElement>("[data-delete-doc]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.deleteDoc;
          if (id && confirm("Bạn có chắc chắn muốn gỡ tài liệu này khỏi kho RAG?")) {
            try {
              await knowledgeApi.remove(id);
              await loadRagDocuments();
            } catch (e) {
              alert(String(e));
            }
          }
        });
      });
    } catch {
      ragTableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:24px;color:#dc2626;">Lỗi kết nối tới Knowledge API.</td></tr>`;
    }
  }

  root.querySelector("#btn-refresh-rag")?.addEventListener("click", () => {
    void loadRagDocuments();
  });

  ragUploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    ragStatus.textContent = "Đang nạp và lập chỉ mục...";
    ragStatus.style.color = "var(--chat-primary)";
    try {
      const fileToUpload = ragKind.value !== "text" ? ragFile.files?.[0] : undefined;
      await knowledgeApi.ingest(
        ragKind.value as any,
        ragSource.value.trim(),
        ragText.value.trim(),
        fileToUpload,
      );
      ragStatus.textContent = "Nạp thành công! Hệ thống đang xử lý.";
      ragStatus.style.color = "#16a34a";
      ragSource.value = "";
      ragText.value = "";
      ragFile.value = "";
      await loadRagDocuments();
    } catch (err) {
      ragStatus.textContent = `Lỗi: ${err instanceof Error ? err.message : String(err)}`;
      ragStatus.style.color = "#dc2626";
    }
  });

  // Modal
  const docModal = root.querySelector<HTMLDialogElement>("#chat-doc-modal")!;
  root.querySelector("#modal-doc-close")?.addEventListener("click", () => docModal.close());

  async function showDocDetail(id: string): Promise<void> {
    try {
      const res = await knowledgeApi.detail(id);
      const detail = res.data;
      root.querySelector<HTMLElement>("#modal-doc-title")!.textContent = detail.source;
      root.querySelector<HTMLElement>("#modal-doc-meta")!.textContent = `${detail.kind.toUpperCase()} · Trạng thái: ${detail.status} · ${new Date(detail.created_at).toLocaleString("vi-VN")}`;
      root.querySelector<HTMLElement>("#modal-doc-text")!.textContent = detail.text || "Không có văn bản đi kèm.";
      docModal.showModal();
    } catch {
      alert("Không lấy được chi tiết tài liệu.");
    }
  }

  // =========================================================================
  // ANALYSIS CASE MEMORY (separate namespace from Knowledge RAG)
  // =========================================================================
  const knowledgePanel = root.querySelector<HTMLElement>("#rag-knowledge-panel")!;
  const casePanel = root.querySelector<HTMLElement>("#rag-case-panel")!;
  const caseTabs = root.querySelectorAll<HTMLButtonElement>("[data-rag-tab]");

  caseTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      const showCases = btn.dataset.ragTab === "cases";
      knowledgePanel.classList.toggle("active", !showCases);
      casePanel.classList.toggle("active", showCases);
      caseTabs.forEach((item) => item.classList.toggle("active", item === btn));
    });
  });

  const caseForm = root.querySelector<HTMLFormElement>("#case-upload-form")!;
  const caseSymbol = root.querySelector<HTMLInputElement>("#case-symbol")!;
  const caseTimeframe = root.querySelector<HTMLSelectElement>("#case-timeframe")!;
  const caseAnalyzedAt = root.querySelector<HTMLInputElement>("#case-analyzed-at")!;
  const caseThesis = root.querySelector<HTMLTextAreaElement>("#case-thesis")!;
  const casePriceBasis = root.querySelector<HTMLSelectElement>("#case-price-basis")!;
  const caseSource = root.querySelector<HTMLInputElement>("#case-source")!;
  const caseChart = root.querySelector<HTMLInputElement>("#case-chart")!;
  const caseSubmit = root.querySelector<HTMLButtonElement>("#case-submit-btn")!;
  const caseStatus = root.querySelector<HTMLElement>("#case-upload-status")!;
  const caseDetail = root.querySelector<HTMLElement>("#case-detail")!;
  const caseLookupInput = root.querySelector<HTMLInputElement>("#case-lookup-id")!;
  const caseLookupBtn = root.querySelector<HTMLButtonElement>("#case-lookup-btn")!;

  const CASE_TERMINAL = new Set(["COMPLETED", "PARTIAL", "NEEDS_REVIEW", "FAILED", "REMOVED"]);
  let casePollTimer: number | undefined;

  function caseStatusBadge(status: string): string {
    return `<span class="rag-badge ${escapeHtml(status.toLowerCase())}">${escapeHtml(status)}</span>`;
  }

  function renderCaseJob(job: AnalysisJob): string {
    return `
      <div class="rag-case-section-title">Tiến trình nạp</div>
      <div class="rag-case-job">
        ${caseStatusBadge(job.status)}
        <span class="rag-case-meta">${escapeHtml(job.stage)} · ${job.progress}% · lần thử ${job.attempts}</span>
        ${job.error_code ? `<div class="rag-case-error">Lỗi: ${escapeHtml(job.error_code)}</div>` : ""}
      </div>
    `;
  }

  function renderCaseDetail(record: AnalysisCaseRecord, job: AnalysisJob | null = null): void {
    const analyzed = record.analyzed_at
      ? new Date(record.analyzed_at).toLocaleString("vi-VN")
      : "—";
    const created = record.created_at
      ? new Date(record.created_at).toLocaleString("vi-VN")
      : "—";
    const verification = record.enrichment?.verification_summary;
    caseDetail.innerHTML = `
      <div class="rag-case-head">
        <div>
          <strong>${escapeHtml(record.symbol || "?")}</strong>
          <span class="rag-case-meta">${escapeHtml(record.timeframe || "?")} · as-of ${escapeHtml(analyzed)}</span>
        </div>
        ${caseStatusBadge(record.ingest_status)}
      </div>
      <dl class="rag-case-kv">
        <dt>Case ID</dt><dd><code>${escapeHtml(record.id)}</code></dd>
        <dt>Nạp lúc</dt><dd>${escapeHtml(created)}</dd>
        ${
          record.chart_path
            ? `<dt>Chart</dt><dd><button type="button" class="rag-action-btn" data-case-chart="${escapeHtml(record.id)}">Xem chart</button></dd>`
            : ""
        }
        ${
          verification
            ? `<dt>Xác minh</dt><dd><code>${escapeHtml(JSON.stringify(verification))}</code></dd>`
            : ""
        }
      </dl>
      <div class="rag-case-section-title">Luận điểm</div>
      <pre class="rag-case-thesis">${escapeHtml(record.thesis)}</pre>
      ${job ? renderCaseJob(job) : ""}
      ${
        record.outcome
          ? `<div class="rag-case-section-title">Outcome</div><pre class="rag-case-thesis">${escapeHtml(JSON.stringify(record.outcome, null, 2))}</pre>`
          : ""
      }
    `;

    caseDetail.querySelector<HTMLButtonElement>("[data-case-chart]")?.addEventListener("click", async (event) => {
      const btn = event.currentTarget as HTMLButtonElement;
      btn.disabled = true;
      try {
        const blob = await analysisCaseApi.chart(record.id);
        const url = URL.createObjectURL(blob);
        window.open(url, "_blank", "noopener");
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } catch (err) {
        alert(err instanceof Error ? err.message : String(err));
      } finally {
        btn.disabled = false;
      }
    });
  }

  function stopCasePolling(): void {
    if (casePollTimer !== undefined) {
      window.clearTimeout(casePollTimer);
      casePollTimer = undefined;
    }
  }

  function scheduleCasePoll(jobId: string, caseId: string, attempt: number): void {
    stopCasePolling();
    if (attempt >= 60) return;
    casePollTimer = window.setTimeout(() => {
      void pollCaseJob(jobId, caseId, attempt + 1);
    }, 3000);
  }

  async function pollCaseJob(jobId: string, caseId: string, attempt: number): Promise<void> {
    try {
      const [jobRes, caseRes] = await Promise.all([
        analysisCaseApi.job(jobId),
        analysisCaseApi.get(caseId),
      ]);
      renderCaseDetail(caseRes.data, jobRes.data);
      if (!CASE_TERMINAL.has(jobRes.data.status)) scheduleCasePoll(jobId, caseId, attempt);
    } catch (err) {
      if (attempt < 20) {
        scheduleCasePoll(jobId, caseId, attempt);
      } else {
        caseStatus.textContent = `Không theo dõi được job: ${err instanceof Error ? err.message : String(err)}`;
        caseStatus.style.color = "#dc2626";
      }
    }
  }

  caseForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const symbol = caseSymbol.value.trim().toUpperCase();
    const thesis = caseThesis.value.trim();
    if (!symbol || !thesis) {
      caseStatus.textContent = "Cần nhập mã cổ phiếu và luận điểm.";
      caseStatus.style.color = "#dc2626";
      return;
    }
    const metadata: Record<string, unknown> = {
      symbol,
      timeframe: caseTimeframe.value,
      thesis,
      source: caseSource.value.trim() || "user",
      price_basis: casePriceBasis.value,
    };
    if (caseAnalyzedAt.value) {
      metadata.analyzed_at = new Date(caseAnalyzedAt.value).toISOString();
    }
    caseSubmit.disabled = true;
    caseStatus.textContent = "Đang gửi case và tạo job nạp...";
    caseStatus.style.color = "var(--chat-primary)";
    try {
      const created = await analysisCaseApi.create(metadata, caseChart.files?.[0]);
      const record = created.data;
      caseLookupInput.value = record.id;
      renderCaseDetail(record);
      caseStatus.textContent = created.job_id
        ? `Đã tạo case. Job ${created.job_id.slice(0, 8)}… đang xử lý.`
        : `Đã tạo case ${record.id.slice(0, 8)}…`;
      caseStatus.style.color = "#16a34a";
      caseThesis.value = "";
      caseChart.value = "";
      if (created.job_id) scheduleCasePoll(created.job_id, record.id, 0);
    } catch (err) {
      caseStatus.textContent = `Lỗi: ${err instanceof Error ? err.message : String(err)}`;
      caseStatus.style.color = "#dc2626";
    } finally {
      caseSubmit.disabled = false;
    }
  });

  async function openCase(id: string): Promise<void> {
    const trimmed = id.trim();
    if (!trimmed) return;
    caseLookupBtn.disabled = true;
    try {
      const res = await analysisCaseApi.get(trimmed);
      stopCasePolling();
      renderCaseDetail(res.data);
      caseStatus.textContent = "";
    } catch (err) {
      caseStatus.textContent = `Không mở được case: ${err instanceof Error ? err.message : String(err)}`;
      caseStatus.style.color = "#dc2626";
    } finally {
      caseLookupBtn.disabled = false;
    }
  }

  caseLookupBtn.addEventListener("click", () => void openCase(caseLookupInput.value));
  caseLookupInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void openCase(caseLookupInput.value);
    }
  });
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

const STATUS_ICON: Record<AssessmentStatus, string> = {
  positive: "✓",
  negative: "✕",
  neutral: "•",
  unknown: "?",
};

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("vi-VN", { maximumFractionDigits: 0 });
}

function safeUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function renderBullets(items: string[], tone: "support" | "risk"): string {
  if (!items.length) return `<div class="analysis-empty">Chưa có.</div>`;
  return `<ul class="analysis-list ${tone}">${items
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("")}</ul>`;
}

function renderResearchReport(report: ResearchReport): string {
  const rows = report.indicators
    .map(
      (row) => `
        <tr>
          <td class="analysis-indicator-label">${escapeHtml(row.label)}</td>
          <td class="analysis-status ${row.status}" title="${escapeHtml(row.status)}">${STATUS_ICON[row.status] || "?"}</td>
          <td>${escapeHtml(row.note)}</td>
        </tr>`,
    )
    .join("");
  const levels = report.watch_levels.length
    ? `<div class="analysis-levels">${report.watch_levels
        .map(
          (level) =>
            `<span class="analysis-level-chip"><strong>${escapeHtml(level.label)}</strong> ${formatPrice(level.price)}</span>`,
        )
        .join("")}</div>`
    : "";
  const changeClass = (report.change_pct ?? 0) >= 0 ? "up" : "down";
  const change =
    report.change_pct === null || report.change_pct === undefined
      ? ""
      : `<span class="analysis-change ${changeClass}">${report.change_pct >= 0 ? "+" : ""}${report.change_pct.toFixed(2).replace(".", ",")}%</span>`;
  const showHistory = Boolean(report.history) && report.history !== "Chưa có case tương tự.";
  const showNews = Boolean(report.news) && report.news !== "Chưa có tin tức phù hợp.";
  const newsItems = Array.isArray(report.news_items) ? report.news_items : [];
  const newsHtml =
    showNews || newsItems.length
      ? `<div class="analysis-section-title">Tin tức & bối cảnh</div>
         ${showNews ? `<p class="analysis-note-block">${escapeHtml(report.news)}</p>` : ""}
         ${
           newsItems.length
             ? `<ul class="analysis-news-list">${newsItems
                 .map((item) => {
                   const url = safeUrl(item.url);
                   const meta = [item.publisher, item.published_at, item.scope === "market" ? "Toàn thị trường" : "Doanh nghiệp"]
                     .filter(Boolean)
                     .map((part) => escapeHtml(String(part)))
                     .join(" · ");
                   return `<li>${
                     url
                       ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>`
                       : escapeHtml(item.title)
                   }<span class="analysis-news-meta">${meta}</span></li>`;
                 })
                 .join("")}</ul>`
             : ""
         }`
      : "";
  return `
    <div class="analysis-report">
      <div class="analysis-report-head">
        <div>
          <span class="analysis-symbol">${escapeHtml(report.symbols.join(", ") || "—")}</span>
          <span class="analysis-meta">${escapeHtml(report.timeframe)}</span>
        </div>
        <span class="analysis-date">${escapeHtml(report.as_of)}</span>
      </div>
      <div class="analysis-price-row">
        <strong>${formatPrice(report.price)}</strong>
        ${change}
      </div>
      <p class="analysis-summary">${escapeHtml(report.summary)}</p>
      <div class="analysis-section-title">Bảng chỉ báo</div>
      <div class="analysis-table-wrap">
        <table class="analysis-table">
          <thead>
            <tr><th>Chỉ báo</th><th>Đánh giá</th><th>Ghi chú</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="analysis-columns">
        <div>
          <div class="analysis-section-title">Điểm hỗ trợ</div>
          ${renderBullets(report.supports, "support")}
        </div>
        <div>
          <div class="analysis-section-title">Rủi ro / theo dõi</div>
          ${renderBullets(report.risks, "risk")}
        </div>
      </div>
      ${levels ? `<div class="analysis-section-title">Mốc giá cần theo dõi</div>${levels}` : ""}
      ${showHistory ? `<div class="analysis-section-title">Case tương tự</div><p class="analysis-note-block">${escapeHtml(report.history)}</p>` : ""}
      ${newsHtml}
      <div class="analysis-data-note">${escapeHtml(report.data_note)}</div>
    </div>`;
}
