import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { PrivyProvider } from "@privy-io/expo";
import { fetchSession, logout } from "./api/auth";
import { ApiError } from "./api/client";
import { hydrateAuthStore, useAuthSession } from "./authStore";
import { requestUnlock } from "./biometricLock";
import { AppWindow } from "./components/AppWindow";
import { PRIVY_APP_ID, PRIVY_CLIENT_ID } from "./config";
import {
  consumeInitialNotificationTap,
  reconcileUnreadFromPresented,
  registerForegroundUnreadListener,
  registerNotificationTapListener,
  setupPushNotifications,
} from "./push";
import { localReverifyStatus } from "./totpReverifyStore";
import { clearUnread, hydrateUnreadStore } from "./unreadStore";
import { PrivyLoginScreen } from "./screens/PrivyLoginScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { ConsoleScreen } from "./screens/ConsoleScreen";
import { HomeScreen, type AppId } from "./screens/HomeScreen";
import { LockScreen } from "./screens/LockScreen";
import { StopScreen } from "./screens/StopScreen";
import { TotpReverifyScreen } from "./screens/TotpReverifyScreen";
import { theme } from "./theme";

type Phase = "loading" | "privy_login" | "locked" | "totp_reverify" | "home";

const APP_TITLES: Record<AppId, string> = {
  chat: "Chat ARIA",
  console: "Console",
  stop: "Kill-switch",
};

function AppShell() {
  const { token, hydrated } = useAuthSession();
  const [phase, setPhase] = useState<Phase>("loading");
  const [unlockFailed, setUnlockFailed] = useState(false);
  const [openApp, setOpenApp] = useState<AppId | null>(null);
  const [pendingChatOpen, setPendingChatOpen] = useState(false);

  useEffect(() => {
    hydrateAuthStore();
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (!token) {
      setPhase("privy_login");
      return;
    }
    // A session already exists (persisted from a previous launch) -- gate
    // local access with biometrics, never re-ask for Privy here. Once
    // unlocked, the session's own totp_reverify_required decides between
    // "home" and the 30-day re-check screen.
    let cancelled = false;
    (async () => {
      const unlocked = await requestUnlock();
      if (cancelled) return;
      if (!unlocked) {
        setPhase("locked");
        setUnlockFailed(true);
        return;
      }
      try {
        const session = await fetchSession();
        if (cancelled) return;
        setPhase(session.totp_reverify_required ? "totp_reverify" : "home");
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          await logout();
          setPhase("privy_login");
          return;
        }
        // Offline at launch -- the session is presumed still valid (never
        // locking the operator out for a transient network hiccup), but the
        // 30-day TOTP policy must still hold: real bug found by the 08/07
        // post-push review, this branch used to go straight to "home"
        // regardless, silently bypassing the re-check whenever the server
        // couldn't be reached. Falls back to the LOCAL mirror -- "overdue"
        // (a real recorded timestamp IS stale) is the only case that still
        // fails closed; "unknown" (nothing ever recorded, e.g. an existing
        // session from before this store existed) degrades to trusting the
        // session rather than a lockout totp_reverify itself couldn't
        // resolve offline (second Devil's Advocate finding on this same fix).
        const status = await localReverifyStatus();
        if (cancelled) return;
        setPhase(status === "overdue" ? "totp_reverify" : "home");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hydrated, token]);

  function retryUnlock() {
    requestUnlock().then((unlocked) => {
      setPhase(unlocked ? "home" : "locked");
      setUnlockFailed(!unlocked);
    });
  }

  // Native push setup (07/08): only once the operator is actually past the
  // lock screen -- never solicits notification permission before that.
  useEffect(() => {
    if (phase !== "home") return;
    let cancelled = false;
    (async () => {
      await hydrateUnreadStore();
      await reconcileUnreadFromPresented();
      if (cancelled) return;
      await setupPushNotifications();
      // 08/07 -- deep-link gap found by the post-push review: a tap that
      // launched/resumed the app (no live event for that, only this
      // one-shot check) requests the chat -- consumed below only once
      // `phase === "home"` is already true, i.e. AFTER the biometric/TOTP
      // gate, never a way around it.
      const launchedByTap = await consumeInitialNotificationTap();
      if (cancelled) return;
      if (launchedByTap) setPendingChatOpen(true);
    })();
    const unsubscribeUnread = registerForegroundUnreadListener();
    const unsubscribeTap = registerNotificationTapListener(() => setPendingChatOpen(true));
    return () => {
      cancelled = true;
      unsubscribeUnread();
      unsubscribeTap();
    };
  }, [phase]);

  useEffect(() => {
    if (phase !== "home" || !pendingChatOpen) return;
    setOpenApp("chat");
    clearUnread();
    setPendingChatOpen(false);
  }, [phase, pendingChatOpen]);

  function handleOpenApp(id: AppId) {
    setOpenApp(id);
    if (id === "chat") clearUnread();
  }

  return (
    <View style={styles.root}>
      <StatusBar style="light" />
      {phase === "loading" && (
        <View style={styles.center}>
          <ActivityIndicator color={theme.accent} />
        </View>
      )}
      {phase === "privy_login" && <PrivyLoginScreen onLoggedIn={() => setPhase("home")} />}
      {phase === "locked" && <LockScreen onRetry={retryUnlock} failed={unlockFailed} />}
      {phase === "totp_reverify" && <TotpReverifyScreen onVerified={() => setPhase("home")} />}
      {phase === "home" && (
        <>
          <HomeScreen onOpenApp={handleOpenApp} onLoggedOut={() => setPhase("privy_login")} />
          <AppWindow
            visible={openApp !== null}
            title={openApp ? APP_TITLES[openApp] : ""}
            onClose={() => setOpenApp(null)}
          >
            {openApp === "chat" && (
              <ChatScreen onLoggedOut={() => { setOpenApp(null); setPhase("privy_login"); }} />
            )}
            {openApp === "console" && <ConsoleScreen />}
            {openApp === "stop" && <StopScreen />}
          </AppWindow>
        </>
      )}
    </View>
  );
}

export default function App() {
  return (
    <PrivyProvider appId={PRIVY_APP_ID} clientId={PRIVY_CLIENT_ID}>
      <SafeAreaProvider>
        <AppShell />
      </SafeAreaProvider>
    </PrivyProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
});
