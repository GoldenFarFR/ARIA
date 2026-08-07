// Unread badge counter for the Chat icon on the home screen (07/08 operator
// request: "un suivi complet" -- know at a glance that ARIA sent something
// while the app was closed). Same module-level + useSyncExternalStore pattern
// as authStore.ts, persisted via SecureStore for consistency with the rest of
// this app (no AsyncStorage dependency added just for one integer).
import { useSyncExternalStore } from "react";
import * as SecureStore from "expo-secure-store";

const UNREAD_KEY = "aria_ops_unread_count";

type Listener = () => void;

let count = 0;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): number {
  return count;
}

async function persist(): Promise<void> {
  try {
    await SecureStore.setItemAsync(UNREAD_KEY, String(count));
  } catch {
    // best-effort -- an unpersisted badge just resets to 0 on next launch,
    // never a crash.
  }
}

export async function hydrateUnreadStore(): Promise<void> {
  try {
    const stored = await SecureStore.getItemAsync(UNREAD_KEY);
    count = stored ? parseInt(stored, 10) || 0 : 0;
  } catch {
    count = 0;
  }
  emit();
}

export function incrementUnread(): void {
  count += 1;
  emit();
  void persist();
}

/** Sets an exact value rather than accumulating -- used to reconcile against
 * the OS notification tray at launch (idempotent across repeated launches,
 * unlike incrementUnread which would double-count the same tray entries). */
export function setUnreadCount(next: number): void {
  count = next;
  emit();
  void persist();
}

export function clearUnread(): void {
  if (count === 0) return;
  count = 0;
  emit();
  void persist();
}

export function useUnreadCount(): number {
  return useSyncExternalStore(subscribe, getSnapshot);
}
