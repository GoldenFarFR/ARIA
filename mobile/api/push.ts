import { apiClient } from "./client";

/** Registers (or refreshes) this device's Expo push token with the backend.
 * Called on every app launch (idempotent upsert server-side) -- a reinstall
 * gets a fresh token that must replace the stale one. */
export async function registerPushToken(token: string, installationId: string): Promise<void> {
  await apiClient.post(
    "/api/aria/ops/push-token",
    { token, installation_id: installationId },
    true,
  );
}
