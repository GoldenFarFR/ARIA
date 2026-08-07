// Local mirror of the backend's last_totp_reverify_at (07/08 operator spec:
// "le totp 1 seul fois tous les 30 jours"). Real bug found by the post-push
// Devil's Advocate review (report 40ba8146, verified against the code before
// fixing): App.tsx's offline fallback at launch (fetchSession() failing on a
// transient network error, not a 401/403) went straight to "home" WITHOUT
// ever evaluating totp_reverify_required -- a dropped connection at launch
// silently bypassed the entire 30-day policy. This store lets App.tsx apply
// the same rule LOCALLY when the server can't be reached, instead of
// defaulting to "trust it".
import * as SecureStore from "expo-secure-store";

const KEY = "aria_ops_last_totp_reverify_at";
const REVERIFY_INTERVAL_MS = 30 * 24 * 60 * 60 * 1000;

export async function markReverifiedNow(): Promise<void> {
  try {
    await SecureStore.setItemAsync(KEY, new Date().toISOString());
  } catch {
    // best-effort -- worst case, the offline fallback below fails closed
    // (treats a missing value as overdue) on the next launch instead
  }
}

/** True if unknown (never recorded) or older than 30 days -- fails CLOSED,
 * same doctrine as the backend's own needs_totp_reverify. Only meant to be
 * consulted when the server itself is unreachable; the server's own flag is
 * always authoritative when reachable. */
export async function isReverifyOverdueLocally(): Promise<boolean> {
  try {
    const raw = await SecureStore.getItemAsync(KEY);
    if (!raw) return true;
    const last = new Date(raw).getTime();
    if (Number.isNaN(last)) return true;
    return Date.now() - last >= REVERIFY_INTERVAL_MS;
  } catch {
    return true;
  }
}
