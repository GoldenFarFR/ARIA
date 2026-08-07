import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { reverifyTotp } from "../api/privyAuth";
import { ApiError, NetworkError } from "../api/client";
import { theme } from "../theme";

// The 30-day periodic re-check (07/08 operator spec: "le totp 1 seul fois
// tous les 30 jours") -- blocks access to the rest of the app until a fresh
// TOTP code confirms this is still the operator, bounding how long a
// compromised device/session stays usable even though the underlying
// session itself never expires on its own (SESSION_TTL).
export function TotpReverifyScreen({ onVerified }: { onVerified: () => void }) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (submitting || code.trim().length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      await reverifyTotp(code.trim());
      onVerified();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("Code invalide ou déjà utilisé — attends le prochain code.");
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Trop de tentatives — patiente un instant avant de réessayer.");
      } else if (err instanceof NetworkError) {
        setError("Hors ligne — impossible de joindre le serveur.");
      } else {
        setError("Vérification impossible pour le moment.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <KeyboardAvoidingView
        style={styles.inner}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={styles.brand}>
          <Text style={styles.title}>Vérification périodique</Text>
          <Text style={styles.subtitle}>
            Tous les 30 jours, ARIA redemande ton code authenticator avant de continuer.
          </Text>
        </View>

        <TextInput
          style={styles.input}
          value={code}
          onChangeText={setCode}
          keyboardType="number-pad"
          maxLength={10}
          placeholder="000000"
          placeholderTextColor={theme.textFaint}
          autoFocus
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <TouchableOpacity
          style={[styles.button, (submitting || code.trim().length === 0) && styles.buttonDisabled]}
          onPress={submit}
          disabled={submitting || code.trim().length === 0}
        >
          {submitting ? (
            <ActivityIndicator color={theme.accentOn} />
          ) : (
            <Text style={styles.buttonText}>Confirmer</Text>
          )}
        </TouchableOpacity>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  inner: { flex: 1, padding: 26, justifyContent: "center", gap: 18 },
  brand: { alignItems: "center", marginBottom: 10, gap: 8 },
  title: { color: theme.text, fontSize: 19, fontWeight: "600", textAlign: "center" },
  subtitle: { color: theme.textDim, fontSize: 13, textAlign: "center", lineHeight: 19 },
  input: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 14,
    color: theme.text,
    fontSize: 22,
    textAlign: "center",
    letterSpacing: 6,
  },
  error: { color: theme.danger, fontSize: 13, textAlign: "center" },
  button: {
    backgroundColor: theme.accent,
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: "center",
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: theme.accentOn, fontSize: 15, fontWeight: "600" },
});
