import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { logout } from "../api/auth";
import { theme } from "../theme";

export type AppId = "chat" | "console";

interface AppIcon {
  id: AppId;
  label: string;
  glyph: string;
}

const APPS: AppIcon[] = [
  { id: "chat", label: "Chat ARIA", glyph: "A" },
  { id: "console", label: "Console", glyph: "📈" },
];

// Smartphone-style home screen (operator request, 07/08): a grid of app
// icons, one tap opens the corresponding screen full-screen (App.tsx wraps
// it in AppWindow, which handles the close-X). Replaces the previous
// straight-to-chat flow -- more apps land here over time (e.g. the
// dedicated STOP button noted as open in HANDOFF_OPERATOR_MOBILE.md), each
// just another entry in APPS.
export function HomeScreen({
  onOpenApp,
  onLoggedOut,
}: {
  onOpenApp: (id: AppId) => void;
  onLoggedOut: () => void;
}) {
  return (
    <SafeAreaView style={styles.root} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Text style={styles.title}>ARIA</Text>
        <TouchableOpacity
          onPress={async () => {
            await logout();
            onLoggedOut();
          }}
        >
          <Text style={styles.logout}>Déconnexion</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.grid}>
        {APPS.map((app) => (
          <TouchableOpacity
            key={app.id}
            style={styles.iconTile}
            onPress={() => onOpenApp(app.id)}
            activeOpacity={0.7}
          >
            <View style={styles.icon}>
              <Text style={styles.iconGlyph}>{app.glyph}</Text>
            </View>
            <Text style={styles.iconLabel}>{app.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: 14,
    paddingHorizontal: 20,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.borderSoft,
  },
  title: { color: theme.text, fontSize: 16, fontWeight: "600" },
  logout: { color: theme.textDim, fontSize: 12.5 },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 20,
    padding: 24,
  },
  iconTile: { width: 84, alignItems: "center", gap: 8 },
  icon: {
    width: 64,
    height: 64,
    borderRadius: 18,
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: "center",
    justifyContent: "center",
  },
  iconGlyph: { fontSize: 26, color: theme.accent },
  iconLabel: { color: theme.textDim, fontSize: 11.5, textAlign: "center" },
});
