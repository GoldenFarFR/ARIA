import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { hydrateAuthStore, useAuthSession } from "./authStore";
import { requestUnlock } from "./biometricLock";
import { AppWindow } from "./components/AppWindow";
import {
  reconcileUnreadFromPresented,
  registerForegroundUnreadListener,
  setupPushNotifications,
} from "./push";
import { clearUnread, hydrateUnreadStore } from "./unreadStore";
import { LoginScreen } from "./screens/LoginScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { ConsoleScreen } from "./screens/ConsoleScreen";
import { HomeScreen, type AppId } from "./screens/HomeScreen";
import { LockScreen } from "./screens/LockScreen";
import { StopScreen } from "./screens/StopScreen";
import { theme } from "./theme";

type Phase = "loading" | "login" | "locked" | "home";

const APP_TITLES: Record<AppId, string> = {
  chat: "Chat ARIA",
  console: "Console",
  stop: "Kill-switch",
};

export default function App() {
  const { token, hydrated } = useAuthSession();
  const [phase, setPhase] = useState<Phase>("loading");
  const [unlockFailed, setUnlockFailed] = useState(false);
  const [openApp, setOpenApp] = useState<AppId | null>(null);

  useEffect(() => {
    hydrateAuthStore();
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (!token) {
      setPhase("login");
      return;
    }
    // A session already exists (persisted from a previous launch) -- gate
    // local access with biometrics, but never re-ask for password/TOTP here.
    let cancelled = false;
    requestUnlock().then((unlocked) => {
      if (cancelled) return;
      setPhase(unlocked ? "home" : "locked");
      setUnlockFailed(!unlocked);
    });
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
    })();
    const unsubscribe = registerForegroundUnreadListener();
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [phase]);

  function handleOpenApp(id: AppId) {
    setOpenApp(id);
    if (id === "chat") clearUnread();
  }

  return (
    <SafeAreaProvider>
      <View style={styles.root}>
        <StatusBar style="light" />
        {phase === "loading" && (
          <View style={styles.center}>
            <ActivityIndicator color={theme.accent} />
          </View>
        )}
        {phase === "login" && <LoginScreen onLoggedIn={() => setPhase("home")} />}
        {phase === "locked" && <LockScreen onRetry={retryUnlock} failed={unlockFailed} />}
        {phase === "home" && (
          <>
            <HomeScreen onOpenApp={handleOpenApp} onLoggedOut={() => setPhase("login")} />
            <AppWindow
              visible={openApp !== null}
              title={openApp ? APP_TITLES[openApp] : ""}
              onClose={() => setOpenApp(null)}
            >
              {openApp === "chat" && <ChatScreen onLoggedOut={() => { setOpenApp(null); setPhase("login"); }} />}
              {openApp === "console" && <ConsoleScreen />}
              {openApp === "stop" && <StopScreen />}
            </AppWindow>
          </>
        )}
      </View>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
});
