// Native push notifications (07/08 follow-up to Item #201): 3 Android
// notification channels matched to expo_push.py's CHANNEL_TRADING/SUPPORT/
// DISCUSSION on the backend, so the operator can mute one independently on
// the phone without touching the others.
//
// NOTE (AGENTS.md: "Expo HAS CHANGED", verified against the SDK 57 docs
// before writing this): remote push notifications are unavailable in Expo Go
// on Android since SDK 53 -- this only works in a real development/production
// build (EAS), never in Expo Go. Everything here fails soft (try/catch, no
// throw) so the rest of the app stays fully usable even where push can't work.
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { registerPushToken } from "./api/push";
import { getOrCreateInstallationId } from "./installationId";
import { incrementUnread, setUnreadCount } from "./unreadStore";

export const PUSH_CHANNEL_TRADING = "trading";
export const PUSH_CHANNEL_SUPPORT = "support";
export const PUSH_CHANNEL_DISCUSSION = "discussion";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

async function ensureAndroidChannels(): Promise<void> {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync(PUSH_CHANNEL_TRADING, {
    name: "Trading — achats, ventes, suivi",
    importance: Notifications.AndroidImportance.HIGH,
    sound: "default",
  });
  await Notifications.setNotificationChannelAsync(PUSH_CHANNEL_SUPPORT, {
    name: "Suivi — alertes et rapports",
    importance: Notifications.AndroidImportance.DEFAULT,
    sound: "default",
  });
  await Notifications.setNotificationChannelAsync(PUSH_CHANNEL_DISCUSSION, {
    name: "Discussion avec ARIA",
    importance: Notifications.AndroidImportance.DEFAULT,
    sound: "default",
  });
}

/** Creates the Android channels, asks permission, registers the Expo push
 * token with the backend. Called once per app launch (home screen mount) --
 * idempotent server-side (upsert), so a repeated call is harmless. Never
 * throws: a failure here degrades to "no native push", Telegram remains the
 * channel of record regardless. */
export async function setupPushNotifications(): Promise<void> {
  try {
    await ensureAndroidChannels();

    if (!Device.isDevice) return; // simulator/emulator: no real push token

    const { status: existing } = await Notifications.getPermissionsAsync();
    let status = existing;
    if (status !== "granted") {
      const requested = await Notifications.requestPermissionsAsync();
      status = requested.status;
    }
    if (status !== "granted") return;

    const projectId = Constants.expoConfig?.extra?.eas?.projectId as string | undefined;
    const { data: token } = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : undefined,
    );
    const installationId = await getOrCreateInstallationId();
    await registerPushToken(token, installationId);
  } catch {
    // best-effort, see docstring above
  }
}

/** Bumps the unread badge for any notification received while the app is
 * foregrounded. Returns the unsubscribe function (React effect cleanup). */
export function registerForegroundUnreadListener(): () => void {
  const subscription = Notifications.addNotificationReceivedListener(() => {
    incrementUnread();
  });
  return () => subscription.remove();
}

/** Reconciles the badge against whatever is still sitting in the OS
 * notification tray -- covers arrivals while the app was closed/backgrounded
 * (the listener above only fires in foreground). Sets an exact count rather
 * than accumulating, so calling this on every launch never double-counts the
 * same tray entries. Call BEFORE registering the foreground listener. */
export async function reconcileUnreadFromPresented(): Promise<void> {
  try {
    const presented = await Notifications.getPresentedNotificationsAsync();
    setUnreadCount(presented.length);
  } catch {
    // best-effort
  }
}

/** Deep-link half of push notifications (08/07, gap found by the post-push
 * Devil's Advocate review): a TAP on a notification is meaningless if it
 * only opens the icon grid -- the operator wants the chat, not a scavenger
 * hunt. Two paths, both funneled through the caller (App.tsx), which only
 * ever acts on this once `phase === "home"` -- i.e. AFTER the biometric/TOTP
 * gate, never as a way around it. */

/** App already foregrounded: fires immediately on tap. */
export function registerNotificationTapListener(onTap: () => void): () => void {
  const subscription = Notifications.addNotificationResponseReceivedListener(() => {
    onTap();
  });
  return () => subscription.remove();
}

/** App was closed/backgrounded and the tap is what launched/resumed it --
 * there's no live event for that, only this one-shot check at startup. */
export async function consumeInitialNotificationTap(): Promise<boolean> {
  try {
    const response = await Notifications.getLastNotificationResponseAsync();
    return response !== null;
  } catch {
    return false;
  }
}
