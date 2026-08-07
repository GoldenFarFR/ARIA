// Local mirror of the backend's last_totp_reverify_at (07/08 operator spec:
// "le totp 1 seul fois tous les 30 jours"). Real bug found by the post-push
// Devil's Advocate review (report 40ba8146, verified against the code before
// fixing): App.tsx's offline fallback at launch (fetchSession() failing on a
// transient network error, not a 401/403) went straight to "home" WITHOUT
// ever evaluating totp_reverify_required -- a dropped connection at launch
// silently bypassed the entire 30-day policy. This store lets App.tsx apply
// the same rule LOCALLY when the server can't be reached, instead of
// defaulting to "trust it".
//
// 08/07, second pass -- a follow-up Devil's Advocate review (report
// 384c13e2) found a real deadlock in the first version of this fix: an
// operator with an EXISTING session (created before this store existed) who
// updates the app and then launches it offline for the first time had no
// locally recorded value -- fail-closed treated that as "overdue", routing
// to totp_reverify, an screen that itself requires a network call to
// complete. Impossible offline: the exact lockout the offline branch was
// meant to avoid, just moved one screen over. Fixed by distinguishing
// "never recorded" (degrade to trusting the session, same as before this
// store existed -- no regression) from "recorded and genuinely stale" (the
// only case that still fails closed).
import * as SecureStore from "expo-secure-store";

const KEY = "aria_ops_last_totp_reverify_at";
const REVERIFY_INTERVAL_MS = 30 * 24 * 60 * 60 * 1000;

export type LocalReverifyStatus = "unknown" | "ok" | "overdue";

export async function markReverifiedNow(): Promise<void> {
  try {
    await SecureStore.setItemAsync(KEY, new Date().toISOString());
  } catch {
    // best-effort -- worst case, the next offline launch reads "unknown"
    // (degrades to trusting the session) rather than a fabricated timestamp
  }
}

/** "overdue" fails CLOSED (same doctrine as the backend's own
 * needs_totp_reverify) ONLY when a real recorded timestamp is actually
 * stale -- "unknown" (nothing ever recorded: fresh install migration, or the
 * SecureStore write itself failed) degrades to "ok" rather than locking the
 * operator out of an app they have no offline way to unlock. Only meant to
 * be consulted when the server itself is unreachable; the server's own flag
 * is always authoritative when reachable. */
export async function localReverifyStatus(): Promise<LocalReverifyStatus> {
  try {
    const raw = await SecureStore.getItemAsync(KEY);
    if (!raw) return "unknown";
    const last = new Date(raw).getTime();
    if (Number.isNaN(last)) return "unknown";
    return Date.now() - last >= REVERIFY_INTERVAL_MS ? "overdue" : "ok";
  } catch {
    return "unknown";
  }
}
