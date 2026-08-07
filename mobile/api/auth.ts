import { apiClient } from "./client";
import { setAuthToken } from "../authStore";

export interface LoginPayload {
  username: string;
  password: string;
  installationId: string;
}

export interface SessionInfo {
  username: string;
  role: string;
  expires_at: string;
  backend_version: string;
  mobile_api: number;
  minimum_mobile_api: number;
}

/** Throws ApiError(401) on wrong credentials, ApiError(429) if rate-limited --
 * the login screen distinguishes these two cases in its error message. */
export async function login(payload: LoginPayload): Promise<void> {
  const { token } = await apiClient.post<{ token: string }>(
    "/api/aria/ops/login",
    {
      username: payload.username,
      password: payload.password,
      installation_id: payload.installationId,
    },
    false,
  );
  await setAuthToken(token);
}

export async function logout(): Promise<void> {
  try {
    await apiClient.post("/api/aria/ops/logout");
  } finally {
    // Always clear locally, even if the network call failed -- the app must
    // never be stuck "logged in" locally when the user asked to log out.
    await setAuthToken(null);
  }
}

/** Verifies + renews the session in one round trip. Callers should log the
 * user out locally on any failure (expired/revoked session). */
export async function fetchSession(): Promise<SessionInfo> {
  return apiClient.get<SessionInfo>("/api/aria/ops/session");
}
