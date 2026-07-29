import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { theme } from "../theme";

export function LockScreen({ onRetry, failed }: { onRetry: () => void; failed: boolean }) {
  return (
    <View style={styles.screen}>
      <View style={styles.mark}>
        <Text style={styles.markText}>A</Text>
      </View>
      <Text style={styles.title}>ARIA App verrouillée</Text>
      <Text style={styles.subtitle}>
        {failed
          ? "Authentification annulée ou échouée."
          : "Déverrouille pour reprendre ta session déjà active."}
      </Text>
      <TouchableOpacity style={styles.button} onPress={onRetry}>
        <Text style={styles.buttonText}>Déverrouiller</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: theme.bg,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 32,
  },
  mark: {
    width: 56,
    height: 56,
    borderRadius: 16,
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 20,
  },
  markText: { color: theme.accent, fontSize: 24, fontWeight: "600" },
  title: { color: theme.text, fontSize: 17, fontWeight: "600", marginBottom: 8 },
  subtitle: { color: theme.textFaint, fontSize: 13, textAlign: "center", marginBottom: 28, lineHeight: 19 },
  button: {
    backgroundColor: theme.accent,
    borderRadius: 12,
    paddingVertical: 14,
    paddingHorizontal: 32,
  },
  buttonText: { color: theme.accentOn, fontSize: 14.5, fontWeight: "600" },
});
