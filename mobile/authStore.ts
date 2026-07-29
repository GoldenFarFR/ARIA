// Single source of truth for the Bearer session token -- every screen reads
// through this, never its own copy (plan's explicit requirement).
import { useSyncExternalStore } from "react";
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "aria_ops_session_token";

type Listener = () => void;

let token: string | null = null;
let hydrated = false;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getTokenSnapshot(): string | null {
  return token;
}

function getHydratedSnapshot(): boolean {
  return hydrated;
}

/** Reads the persisted token once at app launch. A read failure (e.g. the
 * Android Keystore rejecting access after the screen-lock was removed) is
 * treated as "logged out" -- never a crash. */
export async function hydrateAuthStore(): Promise<void> {
  try {
    token = await SecureStore.getItemAsync(TOKEN_KEY);
  } catch {
    token = null;
  }
  hydrated = true;
  emit();
}

export async function setAuthToken(next: string | null): Promise<void> {
  token = next;
  try {
    if (next) {
      await SecureStore.setItemAsync(TOKEN_KEY, next);
    } else {
      await SecureStore.deleteItemAsync(TOKEN_KEY);
    }
  } catch {
    // Keystore write failure: the in-memory token still drives this session,
    // but it won't survive an app restart -- acceptable degradation, never a crash.
  }
  emit();
}

export function getAuthToken(): string | null {
  return token;
}

export function useAuthSession(): { token: string | null; hydrated: boolean } {
  const currentToken = useSyncExternalStore(subscribe, getTokenSnapshot);
  const isHydrated = useSyncExternalStore(subscribe, getHydratedSnapshot);
  return { token: currentToken, hydrated: isHydrated };
}
