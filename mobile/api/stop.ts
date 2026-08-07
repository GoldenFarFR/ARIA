import { apiClient } from "./client";

export interface PauseStatus {
  paused: boolean;
  since: string | null;
  by: string | null;
  reason: string | null;
  readable: boolean;
}

export interface KillSwitchResult extends PauseStatus {
  changed: boolean;
}

export async function getPauseStatus(): Promise<PauseStatus> {
  return apiClient.get<PauseStatus>("/api/aria/ops/status");
}

/** Both require a fresh TOTP code -- see _require_fresh_totp on the backend,
 * unrelated to and never weakened by the app's own login (07/08). */
export async function armStop(totpCode: string, reason?: string): Promise<KillSwitchResult> {
  return apiClient.post<KillSwitchResult>("/api/aria/ops/stop", { totp_code: totpCode, reason });
}

export async function liftStop(totpCode: string, reason?: string): Promise<KillSwitchResult> {
  return apiClient.post<KillSwitchResult>("/api/aria/ops/resume", { totp_code: totpCode, reason });
}
