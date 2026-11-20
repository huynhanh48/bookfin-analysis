/** Lớp gọi API backend; mọi URL đi qua proxy `/api` của Vite. */

import type {
  AnalysisCaseResponse,
  AnalysisJob,
  AnalysisSearchResponse,
  EvidenceAnswer,
  IndicatorSeriesResponse,
  IndicatorSetResponse,
  SymbolListResponse,
  StructureSnapshot,
  Timeframe,
} from "./types";

const API_PREFIX = "/api/v1";

async function request<T>(path: string, params?: Record<string, string | number | string[]>): Promise<T> {
  const url = new URL(`${API_PREFIX}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else {
      url.searchParams.set(key, String(value));
    }
  }

  const response = await fetch(url.toString());
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "error" in body
        ? String((body as { error: { message?: string } }).error.message ?? "Yêu cầu thất bại")
        : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body as T;
}

export function fetchSymbols(limit = 300): Promise<SymbolListResponse> {
  return request<SymbolListResponse>("/market/symbols", { limit });
}

export function fetchSeries(
  symbol: string,
  timeframe: Timeframe,
  limit: number,
  indicators: string[],
): Promise<IndicatorSeriesResponse> {
  return request<IndicatorSeriesResponse>(`/indicators/${encodeURIComponent(symbol)}/series`, {
    timeframe,
    limit,
    indicator: indicators,
  });
}

export function fetchIndicators(symbol: string, timeframe: Timeframe): Promise<IndicatorSetResponse> {
  return request<IndicatorSetResponse>(`/indicators/${encodeURIComponent(symbol)}`, { timeframe });
}

export function fetchStructure(symbol: string, timeframe: Timeframe): Promise<StructureSnapshot> {
  return request<StructureSnapshot>(`/indicators/${encodeURIComponent(symbol)}/structure`, {
    timeframe,
  });
}


/* ------------------------------------------------------------------ */
/* Analysis Case Memory / Evidence RAG                                 */
/* ------------------------------------------------------------------ */

function apiError(body: unknown, status: number): Error {
  if (body && typeof body === "object" && "error" in body) {
    const detail = (body as { error?: { message?: string } }).error;
    return new Error(detail?.message ?? "Yêu cầu thất bại");
  }
  return new Error(`HTTP ${status}`);
}

async function analysisRequest<T>(
  path: string,
  init: RequestInit = {},
  adminKey = "",
): Promise<T> {
  const headers = new Headers(init.headers);
  if (adminKey.trim()) headers.set("X-Admin-Key", adminKey.trim());
  const response = await fetch(`${API_PREFIX}${path}`, { ...init, headers });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw apiError(body, response.status);
  return body as T;
}

export function createAnalysisCase(
  metadata: Record<string, unknown>,
  chart: File | null,
  adminKey = "",
): Promise<AnalysisCaseResponse> {
  const form = new FormData();
  form.set("metadata", JSON.stringify(metadata));
  if (chart) form.set("chart", chart);
  return analysisRequest<AnalysisCaseResponse>(
    "/analysis/cases",
    { method: "POST", body: form },
    adminKey,
  );
}

export function getAnalysisCase(caseId: string, adminKey = ""): Promise<AnalysisCaseResponse> {
  return analysisRequest<AnalysisCaseResponse>(
    `/analysis/cases/${encodeURIComponent(caseId)}`,
    {},
    adminKey,
  );
}

export function retryAnalysisCase(caseId: string, adminKey = ""): Promise<AnalysisCaseResponse> {
  return analysisRequest<AnalysisCaseResponse>(
    `/analysis/cases/${encodeURIComponent(caseId)}/retry`,
    { method: "POST" },
    adminKey,
  );
}

export function getAnalysisJob(jobId: string, adminKey = ""): Promise<{ data: AnalysisJob }> {
  return analysisRequest<{ data: AnalysisJob }>(
    `/analysis/jobs/${encodeURIComponent(jobId)}`,
    {},
    adminKey,
  );
}

export function updateAnalysisOutcome(
  caseId: string,
  outcome: Record<string, unknown>,
  adminKey = "",
): Promise<AnalysisCaseResponse> {
  return analysisRequest<AnalysisCaseResponse>(
    `/analysis/cases/${encodeURIComponent(caseId)}/outcome`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome }),
    },
    adminKey,
  );
}

export function searchAnalysisCases(
  payload: Record<string, unknown>,
  adminKey = "",
): Promise<AnalysisSearchResponse> {
  return analysisRequest<AnalysisSearchResponse>(
    "/analysis/search",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    adminKey,
  );
}

export function queryMultimodalAnalysis(
  metadata: Record<string, unknown>,
  image: File | null,
  adminKey = "",
): Promise<{ data: EvidenceAnswer }> {
  const form = new FormData();
  form.set("metadata", JSON.stringify(metadata));
  if (image) form.set("image", image);
  return analysisRequest<{ data: EvidenceAnswer }>(
    "/rag/query/multimodal",
    { method: "POST", body: form },
    adminKey,
  );
}

export async function fetchAnalysisChart(caseId: string, adminKey = ""): Promise<string> {
  const headers = new Headers();
  if (adminKey.trim()) headers.set("X-Admin-Key", adminKey.trim());
  const response = await fetch(
    `${API_PREFIX}/analysis/cases/${encodeURIComponent(caseId)}/chart`,
    { headers },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw apiError(body, response.status);
  }
  return URL.createObjectURL(await response.blob());
}
