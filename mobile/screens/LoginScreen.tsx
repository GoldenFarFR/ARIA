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
import { login } from "../api/auth";
import { ApiError, NetworkError } from "../api/client";
import { getOrCreateInstallationId } from "../installationId";
import { theme } from "../theme";

export function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = username.trim().length > 0 && password.length > 0;

  async function handleSubmit() {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const installationId = await getOrCreateInstallationId();
      await login({ username: username.trim(), password, installationId });
      setPassword("");
      onLoggedIn();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Identifiants ou code invalides.");
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Trop de tentatives — patiente un instant avant de réessayer.");
      } else if (err instanceof NetworkError) {
        setError("Hors ligne — impossible de joindre le serveur.");
      } else {
        setError("Connexion impossible pour le moment.");
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
        <View style={styles.mark}>
          <Text style={styles.markText}>A</Text>
        </View>
        <Text style={styles.title}>ARIA App</Text>
        <Text style={styles.subtitle}>Canal de secours — accès opérateur</Text>
      </View>

      <View style={styles.field}>
        <Text style={styles.label}>Identifiant</Text>
        <TextInput
          style={styles.input}
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="operator"
          placeholderTextColor={theme.textFaint}
        />
      </View>

      <View style={styles.field}>
        <Text style={styles.label}>Mot de passe</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="••••••••••"
          placeholderTextColor={theme.textFaint}
        />
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <TouchableOpacity
        style={[styles.button, (!canSubmit || submitting) && styles.buttonDisabled]}
        onPress={handleSubmit}
        disabled={!canSubmit || submitting}
      >
        {submitting ? (
          <ActivityIndicator color={theme.accentOn} />
        ) : (
          <Text style={styles.buttonText}>Se connecter</Text>
        )}
      </TouchableOpacity>

      <Text style={styles.footnote}>
        Connecté directement à ton VPS — aucune dépendance à Telegram
      </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  inner: {
    flex: 1,
    paddingHorizontal: 26,
    paddingTop: 24,
    paddingBottom: 20,
  },
  brand: { alignItems: "center", marginBottom: 40 },
  mark: {
    width: 56,
    height: 56,
    borderRadius: 16,
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 16,
  },
  markText: { color: theme.accent, fontSize: 24, fontWeight: "600" },
  title: { color: theme.text, fontSize: 21, fontWeight: "600", marginBottom: 6 },
  subtitle: { color: theme.textFaint, fontSize: 13 },
  field: { marginBottom: 18 },
  label: { color: theme.textDim, fontSize: 12, marginBottom: 8 },
  input: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 13,
    color: theme.text,
    fontSize: 15,
  },
  error: { color: theme.danger, fontSize: 13, marginBottom: 14, textAlign: "center" },
  button: {
    backgroundColor: theme.accent,
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: "center",
    marginTop: 6,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: theme.accentOn, fontSize: 15, fontWeight: "600" },
  footnote: {
    marginTop: "auto",
    textAlign: "center",
    color: theme.textFaint,
    fontSize: 11.5,
    lineHeight: 18,
  },
});
