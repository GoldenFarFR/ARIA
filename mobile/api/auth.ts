import { apiClient } from "./client";
import { setAuthToken } from "../authStore";

export interface SessionInfo {
  username: string;
  role: string;
  expires_at: string;
  backend_version: string;
  mobile_api: number;
  minimum_mobile_api: number;
  // 08/07 -- Privy auth redesign, the 30-day periodic re-check.
  totp_reverify_required: boolean;
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
