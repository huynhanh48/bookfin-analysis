/**
 * Shared Admin API Key handling for every same-origin backend call.
 *
 * Production sets ADMIN_API_KEY, so market/chart endpoints also require the
 * X-Admin-Key header. Keeping the key and the 401/403 prompt in one module
 * prevents the chart, watchlist and screener from drifting from chat auth.
 */
const STORAGE_KEY = "bookfin_admin_key";

let adminKey =
  typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) ?? "" : "";
let pendingPrompt: Promise<string> | null = null;

export function getAdminKey(): string {
  return adminKey;
}

export function setAdminKey(value: string): void {
  adminKey = value.trim();
  if (typeof localStorage === "undefined") return;
  if (adminKey) localStorage.setItem(STORAGE_KEY, adminKey);
  else localStorage.removeItem(STORAGE_KEY);
}

export function adminHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  if (adminKey) headers.set("X-Admin-Key", adminKey);
  return headers;
}

async function requestAdminKey(): Promise<string> {
  if (typeof window === "undefined") return "";
  // One prompt for concurrent chart/symbol requests instead of one per call.
  if (!pendingPrompt) {
    pendingPrompt = Promise.resolve(window.prompt("Nhập Admin API Key để tiếp tục:") ?? "")
      .then((entered) => {
        const value = entered.trim();
        if (value) setAdminKey(value);
        return value;
      })
      .finally(() => {
        pendingPrompt = null;
      });
  }
  return pendingPrompt;
}

/** Fetch a backend endpoint with the stored key, prompting once on 401/403. */
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  let response = await fetch(input, { ...init, headers: adminHeaders(init.headers) });
  if ((response.status === 401 || response.status === 403) && !adminKey) {
    const entered = await requestAdminKey();
    if (entered) {
      response = await fetch(input, { ...init, headers: adminHeaders(init.headers) });
    }
  }
  return response;
}
