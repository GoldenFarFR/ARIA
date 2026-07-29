// App-launch biometric lock -- complements the already-persistent 7-day
// session (authStore.ts), it does not replace it: a valid session token still
// lives in SecureStore, this only gates LOCAL access to it on this device.
import * as LocalAuthentication from "expo-local-authentication";

export async function isDeviceLockConfigured(): Promise<boolean> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync();
  const isEnrolled = await LocalAuthentication.isEnrolledAsync();
  return hasHardware && isEnrolled;
}

/** Returns true if unlocked. A device with NO biometric/PIN configured at all
 * is never locked out of its own app -- there is nothing to authenticate
 * against, so we degrade to "already unlocked" rather than stranding the
 * operator. */
export async function requestUnlock(): Promise<boolean> {
  const configured = await isDeviceLockConfigured();
  if (!configured) return true;

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Déverrouiller ARIA App",
    cancelLabel: "Annuler",
    disableDeviceFallback: false,
  });
  return result.success;
}
