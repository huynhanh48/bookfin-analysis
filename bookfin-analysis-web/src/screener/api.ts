import { apiFetch } from "../apiAuth";
import type {
  ScreenerConditions,
  ScreenerScanRequest,
  ScreenerScanResponse,
  WatchlistBulkAddResult,
  WatchlistItemsResponse,
  WatchlistListResponse,
  WatchlistOut,
} from "./types";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${BASE}${path}`, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(10000),
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: { message?: string } };
      message = payload.error?.message ?? message;
    } catch {
      // ignore non-JSON error
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const screenerApi = {
  defaultFilters: () => request<ScreenerConditions>("/screener/filters/default"),
  scan: (payload: ScreenerScanRequest) =>
    request<ScreenerScanResponse>("/screener/scan", {
      method: "POST",
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(45000),
    }),
  results: (profileId: string) =>
    request<ScreenerScanResponse>(
      `/screener/results?profile_id=${encodeURIComponent(profileId)}`,
    ),
};

export const watchlistApi = {
  list: () => request<WatchlistListResponse>("/watchlists"),
  create: (name: string) =>
    request<WatchlistOut>("/watchlists", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  rename: (id: number, name: string) =>
    request<WatchlistOut>(`/watchlists/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  remove: (id: number) =>
    request<void>(`/watchlists/${id}`, { method: "DELETE" }),
  items: (id: number) =>
    request<WatchlistItemsResponse>(`/watchlists/${id}/items`),
  addBulk: (id: number, symbols: string[]) =>
    request<WatchlistBulkAddResult>(`/watchlists/${id}/items/bulk`, {
      method: "POST",
      body: JSON.stringify({ symbols }),
    }),
  removeSymbol: (id: number, symbol: string) =>
    request<void>(`/watchlists/${id}/items/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),
  clear: (id: number) =>
    request<void>(`/watchlists/${id}/items`, { method: "DELETE" }),
};
