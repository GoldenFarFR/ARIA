import React, { useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
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

  return (
    <View style={styles.root}>
      <WebView
        source={{ uri: CONSOLE_URL }}
        style={styles.webview}
        onLoadStart={() => setLoading(true)}
        onLoadEnd={() => setLoading(false)}
        startInLoadingState={false}
      />
      {loading && (
        <View style={[StyleSheet.absoluteFill, styles.loadingOverlay]}>
          <ActivityIndicator color={theme.accent} />
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
  },
});
