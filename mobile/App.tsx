import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import { hydrateAuthStore, useAuthSession } from "./authStore";
import { requestUnlock } from "./biometricLock";
import { LoginScreen } from "./screens/LoginScreen";
import { ChatScreen } from "./screens/ChatScreen";
import { LockScreen } from "./screens/LockScreen";
import { theme } from "./theme";

type Phase = "loading" | "login" | "locked" | "chat";

export default function App() {
  const { token, hydrated } = useAuthSession();
  const [phase, setPhase] = useState<Phase>("loading");
  const [unlockFailed, setUnlockFailed] = useState(false);

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
      setPhase(unlocked ? "chat" : "locked");
      setUnlockFailed(!unlocked);
    });
    return () => {
      cancelled = true;
    };
  }, [hydrated, token]);

  function retryUnlock() {
    requestUnlock().then((unlocked) => {
      setPhase(unlocked ? "chat" : "locked");
      setUnlockFailed(!unlocked);
    });
  }

  return (
    <View style={styles.root}>
      <StatusBar style="light" />
      {phase === "loading" && (
        <View style={styles.center}>
          <ActivityIndicator color={theme.accent} />
        </View>
      )}
      {phase === "login" && <LoginScreen onLoggedIn={() => setPhase("chat")} />}
      {phase === "locked" && <LockScreen onRetry={retryUnlock} failed={unlockFailed} />}
      {phase === "chat" && <ChatScreen onLoggedOut={() => setPhase("login")} />}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
});
