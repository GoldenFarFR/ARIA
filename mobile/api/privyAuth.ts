import { apiClient } from "./client";
import { setAuthToken } from "../authStore";

export interface PrivyLoginPayload {
  privyAccessToken: string;
  inviteCode?: string;
  installationId: string;
}

/** Throws ApiError(403, "invite_code_required") the first time a never-seen
 * Privy identity logs in without a code -- the caller (PrivyLoginScreen)
 * catches exactly this to reveal the invite-code field. */
export async function loginWithPrivy(payload: PrivyLoginPayload): Promise<void> {
  const { token } = await apiClient.post<{ token: string }>(
    "/api/aria/ops/login-privy",
    {
      privy_access_token: payload.privyAccessToken,
      invite_code: payload.inviteCode,
      installation_id: payload.installationId,
    },
    false,
  );
  await setAuthToken(token);
}

/** The 30-day periodic re-check (operator spec). Throws ApiError(403) on a
 * wrong/replayed code, ApiError(429) if rate-limited. */
export async function reverifyTotp(totpCode: string): Promise<void> {
  await apiClient.post("/api/aria/ops/totp-reverify", { totp_code: totpCode });
}
