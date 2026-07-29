// Stable per-install identifier sent as installation_id on login (helps the
// operator recognize a session in a future device list -- never a hardware
// fingerprint, see operator_session.py). Not a secret, but stored alongside
// the token for simplicity (SecureStore already a dependency).
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

const INSTALLATION_ID_KEY = "aria_ops_installation_id";

export async function getOrCreateInstallationId(): Promise<string> {
  try {
    const existing = await SecureStore.getItemAsync(INSTALLATION_ID_KEY);
    if (existing) return existing;
  } catch {
    // fall through to generating a fresh one for this session
  }
  const id = Crypto.randomUUID();
  try {
    await SecureStore.setItemAsync(INSTALLATION_ID_KEY, id);
  } catch {
    // best-effort persistence -- a regenerated id every launch is a cosmetic
    // downgrade only (sessions still work), never a functional break.
  }
  return id;
}
