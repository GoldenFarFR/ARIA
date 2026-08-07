import React, { useRef, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { WebView, type WebViewNavigation } from "react-native-webview";
import { theme } from "../theme";

// ARIA's own chart console (MarketApp, deployed at ops.ariavanguardzhc.com/
// market) inside the app window -- shows every open paper-trading position
// with a real chart, same view the operator already gets via the console
// link in Telegram alerts. Note (not fixed here): /market gates on Privy
// member sign-in (MemberGate), a DIFFERENT auth than this app's own operator
// login -- the operator authenticates once inside the WebView on first use.
const CONSOLE_URL = "https://ops.ariavanguardzhc.com/market";

export function ConsoleScreen() {
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const webviewRef = useRef<WebView>(null);

  function retry() {
    setOffline(false);
    setLoading(true);
    webviewRef.current?.reload();
  }

  return (
    <View style={styles.root}>
      <WebView
        ref={webviewRef}
        source={{ uri: CONSOLE_URL }}
        style={styles.webview}
        onLoadStart={() => {
          setLoading(true);
          setOffline(false);
        }}
        onLoadEnd={() => setLoading(false)}
        // 08/07 -- operator-noted gap: a lost connection used to leave a
        // silent blank/white screen with no way to tell "offline" from
        // "still loading". onError fires for network failures, onHttpError
        // for a reachable-but-erroring server (5xx) -- both degrade to the
        // same banner, the operator doesn't need the distinction.
        onError={() => {
          setLoading(false);
          setOffline(true);
        }}
        onHttpError={() => {
          setLoading(false);
          setOffline(true);
        }}
        startInLoadingState={false}
      />
      {loading && !offline && (
        <View style={[StyleSheet.absoluteFill, styles.loadingOverlay]}>
          <ActivityIndicator color={theme.accent} />
        </View>
      )}
      {offline && (
        <View style={[StyleSheet.absoluteFill, styles.loadingOverlay]}>
          <Text style={styles.offlineText}>Connexion perdue</Text>
          <TouchableOpacity style={styles.retryButton} onPress={retry} activeOpacity={0.7}>
            <Text style={styles.retryText}>Réessayer</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  webview: { flex: 1, backgroundColor: theme.bg },
  loadingOverlay: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.bg,
    gap: 14,
  },
  offlineText: { color: theme.textDim, fontSize: 13.5 },
  retryButton: {
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 20,
  },
  retryText: { color: theme.accent, fontSize: 13.5, fontWeight: "600" },
});
