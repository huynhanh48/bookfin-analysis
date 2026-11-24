import { apiFetch, getAdminKey } from "../apiAuth";

export { setAdminKey } from "../apiAuth";

export type DocumentStatus = "PENDING" | "PROCESSING" | "EXTRACTING" | "ENRICHING" |
  "VERIFYING" | "NORMALIZING" | "INDEXING" | "COMPLETED" | "PARTIAL" |
  "FAILED" | "DELETING" | "REMOVED";

export interface KnowledgeDocument {
  id: string;
  kind: "text" | "image" | "document";
  source: string;
  status: DocumentStatus;
  error_code: string | null;
  stage: string;
  progress: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  text: string;
}

export interface KnowledgeAnswer {
  answer: string;
  sources: Record<string, unknown>[];
  rerank_metadata: Record<string, unknown>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
  sources?: Record<string, unknown>[];
  report?: ResearchReport | null;
  research?: ResearchRun;
  kind?: "text" | "audio" | "image" | "link";
  audioDuration?: string;
  linkTitle?: string;
  linkDesc?: string;
  linkUrl?: string;
  imageUrl?: string;
  reactions?: {
    thumbsUp?: boolean;
    thumbsDown?: boolean;
  };
}

async function request<T>(path: string, init: RequestInit = {}, prefix = "/rag"): Promise<T> {
  const signal = prefix === "/chat"
    ? AbortSignal.timeout(path.endsWith("/turns") ? 340_000 : 10_000)
    : init.signal;
  let response: Response;
  try {
    response = await apiFetch(`/api/v1${prefix}${path}`, { ...init, signal });
  } catch (error) {
    if (signal?.aborted) throw new Error("Máy chủ chat chưa phản hồi. Vui lòng thử lại.");
    throw error;
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json() as { error?: { message?: string }; detail?: unknown };
      message = body.error?.message ?? (typeof body.detail === "string" ? body.detail : message);
    } catch { /* HTTP status remains visible. */ }
    throw new Error(message);
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

export interface Conversation {
  id: string;
  title: string;
  updated_at: string;
}

export interface ChatTurn {
  position: number;
  question: string;
  answer: string;
  sources: Record<string, unknown>[];
  report?: ResearchReport | null;
  created_at: string;
}

export type AssessmentStatus = "positive" | "negative" | "neutral" | "unknown";

export interface IndicatorAssessment {
  key: string;
  label: string;
  value?: string | null;
  status: AssessmentStatus;
  note: string;
}

export interface WatchLevel {
  label: string;
  price: number;
  note?: string;
}

export interface NewsItem {
  title: string;
  publisher: string;
  published_at: string;
  url: string;
  source_type: string;
  scope: "company" | "market";
}

export interface ResearchReport {
  version: string;
  symbols: string[];
  timeframe: string;
  as_of: string;
  price: number | null;
  change_pct: number | null;
  stance: "bullish" | "bearish" | "mixed" | "neutral";
  stance_label: string;
  summary: string;
  indicators: IndicatorAssessment[];
  supports: string[];
  risks: string[];
  watch_levels: WatchLevel[];
  history: string;
  news: string;
  news_items: NewsItem[];
  data_note: string;
  data_freshness?: Record<string, unknown> | null;
  warnings?: string[];
}

function conversationPath(id: string): string {
  return `/conversations/${encodeURIComponent(id)}`;
}

export const chatApi = {
  list: () => request<Conversation[]>("/conversations", {}, "/chat"),
  create: () => request<Conversation>("/conversations", { method: "POST" }, "/chat"),
  detail: (id: string) => request<Conversation & { turns: ChatTurn[] }>(conversationPath(id), {}, "/chat"),
  rename: (id: string, title: string) => request<Conversation>(conversationPath(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }, "/chat"),
  remove: (id: string) => request<void>(conversationPath(id), { method: "DELETE" }, "/chat"),
  send: (id: string, question: string, mode: "knowledge" | "research") =>
    request<ChatTurn>(`${conversationPath(id)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode }),
    }, "/chat"),
};

export const knowledgeApi = {
  list: (cursor?: string) => request<{ data: KnowledgeDocument[]; next_cursor: string | null }>(
    `/documents?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
  ),
  detail: (id: string) => request<{ data: KnowledgeDocumentDetail }>(
    `/documents/${encodeURIComponent(id)}/content`,
  ),
  file: async (id: string): Promise<Blob> => {
    const response = await apiFetch(`/api/v1/rag/documents/${encodeURIComponent(id)}/file`);
    if (!response.ok) throw new Error(`Không tải được file gốc (HTTP ${response.status}).`);
    return response.blob();
  },
  ingest: (kind: KnowledgeDocument["kind"], source: string, text: string, file?: File) => {
    const form = new FormData();
    form.set("metadata", JSON.stringify({ kind, source, text }));
    if (file) form.set("file", file);
    return request<{ data: KnowledgeDocument }>("/documents", { method: "POST", body: form });
  },
  retry: (id: string) => request<{ data: KnowledgeDocument }>(
    `/documents/${encodeURIComponent(id)}/retry`, { method: "POST" },
  ),
  remove: (id: string) => request<{ data: KnowledgeDocument }>(
    `/documents/${encodeURIComponent(id)}`, { method: "DELETE" },
  ),
  ask: (question: string, mode: string) => request<{ data: KnowledgeAnswer }>(
    "/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, mode }),
    },
  ),
};

export interface ResearchRun {
  analysis_id: string;
  symbol: string;
  symbols: string[];
  timeframe: string;
  analyzed_at: string;
  status: "running" | "completed" | "failed";
  markdown: string | null;
  technical_summary: Record<string, { market: Record<string, unknown>; fingerprint: Record<string, string> }>;
  warnings: string[];
}

export const researchApi = {
  watch: (id: string, onProgress: () => void): (() => void) => {
    const url = new URL("/api/v1/ws", window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    const channel = `agent:${id}`;
    socket.onopen = () => {
      const key = getAdminKey();
      if (key) socket.send(JSON.stringify({ type: "authenticate", token: key }));
    };
    socket.onmessage = ({ data }) => {
      try {
        const event = JSON.parse(String(data));
        if (event.type === "ready") socket.send(JSON.stringify({ type: "subscribe", channel }));
        if (event.type === "agent_progress" && event.channel === channel) onProgress();
      } catch { /* Status polling also works if a frame is invalid. */ }
    };
    return () => socket.close();
  },
  start: (question: string) => request<ResearchRun>("/agent/analyze", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }), signal: AbortSignal.timeout(20000),
  }, ""),
  status: (id: string) => request<ResearchRun>("/agent/analyses/" + encodeURIComponent(id), {
    signal: AbortSignal.timeout(12000),
  }, ""),
  save: (run: ResearchRun, symbol: string, thesis: string) => {
    const body = new FormData();
    body.set("metadata", JSON.stringify({
      symbol, timeframe: run.timeframe, analyzed_at: run.analyzed_at, thesis,
      source: "BookFin Research Agent", custom_metadata: { analysis_id: run.analysis_id },
    }));
    return request<{ data: { id: string } }>("/analysis/cases", {
      method: "POST", body, signal: AbortSignal.timeout(30000),
    }, "");
  },
};

export interface AnalysisCaseRecord {
  id: string;
  symbol: string | null;
  timeframe: string | null;
  analyzed_at: string | null;
  thesis: string;
  ingest_status: string;
  outcome?: Record<string, unknown> | null;
  enrichment?: Record<string, unknown>;
  chart_path?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface AnalysisJob {
  id: string;
  case_id: string | null;
  status: string;
  stage: string;
  progress: number;
  attempts: number;
  error_code: string | null;
  diagnostics: Record<string, unknown>;
}

export const analysisCaseApi = {
  create(metadata: Record<string, unknown>, chart?: File) {
    const body = new FormData();
    body.set("metadata", JSON.stringify(metadata));
    if (chart) body.set("chart", chart);
    return request<{ data: AnalysisCaseRecord; job_id: string | null }>(
      "/analysis/cases", { method: "POST", body, signal: AbortSignal.timeout(35000) }, "",
    );
  },
  get(id: string) {
    return request<{ data: AnalysisCaseRecord }>("/analysis/cases/" + encodeURIComponent(id), {}, "");
  },
  job(id: string) {
    return request<{ data: AnalysisJob }>("/analysis/jobs/" + encodeURIComponent(id), {
      signal: AbortSignal.timeout(12000),
    }, "");
  },
  chart: async (id: string): Promise<Blob> => {
    const response = await apiFetch(`/api/v1/analysis/cases/${encodeURIComponent(id)}/chart`);
    if (!response.ok) throw new Error(`Không tải được chart (HTTP ${response.status}).`);
    return response.blob();
  },
  search(query: string) {
    return request<{ data: { case: AnalysisCaseRecord }[] }>("/analysis/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 5 }), signal: AbortSignal.timeout(35000),
    }, "");
  },
};

