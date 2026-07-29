// Thin fetch wrapper shared by auth.ts/chat.ts -- single place for base URL,
// JSON handling, and the "offline vs server error" distinction the plan requires
// (two distinct UI states, never one generic indicator).
import { API_BASE_URL } from "../config";
import { getAuthToken } from "../authStore";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export class NetworkError extends Error {}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean; timeoutMs?: number } = {},
): Promise<T> {
  const { method = "GET", body, auth = false, timeoutMs = 20000 } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    throw new NetworkError(err instanceof Error ? err.message : "network error");
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      // non-JSON error body -- keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiClient = {
  get: <T>(path: string, auth = true) => request<T>(path, { method: "GET", auth }),
  post: <T>(path: string, body?: unknown, auth = true) =>
    request<T>(path, { method: "POST", body, auth }),
  // Cancellable variant for the chat screen (AbortController, client-side only --
  // never stops the server-side AriaBrain.process() call already in flight).
  postCancellable: <T>(path: string, body: unknown, signal: AbortSignal): Promise<T> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    return fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
    }).then(async (response) => {
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const data = await response.json();
          if (typeof data?.detail === "string") detail = data.detail;
        } catch {
          // keep statusText
        }
        throw new ApiError(response.status, detail);
      }
      return response.json() as Promise<T>;
    });
  },
};
