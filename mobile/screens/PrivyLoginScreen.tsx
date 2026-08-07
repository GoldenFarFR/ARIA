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
import { useLoginWithEmail, useLoginWithOAuth, usePrivy } from "@privy-io/expo";
import { loginWithPrivy } from "../api/privyAuth";
import { ApiError, NetworkError } from "../api/client";
import { getOrCreateInstallationId } from "../installationId";
import { theme } from "../theme";

// MetaMask-style flow (07/08 operator spec): sign in with Privy (Google, X,
// or email) -- first-ever sign-in for THIS Privy identity reveals an
// invite-code field (backend rejects with invite_code_required), every
// later sign-in on any device just needs the Privy identity itself, exactly
// like a password. Replaces the old username+password LoginScreen entirely.
type Step = "choose" | "email_address" | "email_code" | "invite_code";

export function PrivyLoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const { getAccessToken } = usePrivy();
  const { login: loginOAuth } = useLoginWithOAuth();
  const { sendCode, loginWithCode } = useLoginWithEmail();

  const [step, setStep] = useState<Step>("choose");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [pendingAccessToken, setPendingAccessToken] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function finishWithBackend(accessToken: string, code?: string) {
    setSubmitting(true);
    setError(null);
    try {
      const installationId = await getOrCreateInstallationId();
      await loginWithPrivy({ privyAccessToken: accessToken, inviteCode: code, installationId });
      onLoggedIn();
    } catch (err) {
      if (err instanceof ApiError && err.status === 403 && err.message === "invite_code_required") {
        setPendingAccessToken(accessToken);
        setStep("invite_code");
      } else if (err instanceof ApiError && err.status === 403) {
        setError("Code d'invitation invalide ou déjà utilisé.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Connexion Privy invalide — réessaie.");
        setStep("choose");
      } else if (err instanceof NetworkError) {
        setError("Hors ligne — impossible de joindre le serveur.");
      } else {
        setError("Connexion impossible pour le moment.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function afterPrivySuccess() {
    const token = await getAccessToken();
    if (!token) {
      setError("Connexion Privy incomplète — réessaie.");
      return;
    }
    await finishWithBackend(token);
  }

  async function handleOAuth(provider: "google" | "twitter") {
    setError(null);
    setSubmitting(true);
    try {
      await loginOAuth({ provider });
      await afterPrivySuccess();
    } catch {
      setError("Connexion impossible pour le moment.");
      setSubmitting(false);
    }
  }

  async function handleSendEmailCode() {
    if (!email.trim()) return;
    setError(null);
    setSubmitting(true);
    try {
      await sendCode({ email: email.trim() });
      setStep("email_code");
    } catch {
      setError("Envoi du code impossible — vérifie l'adresse.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyEmailCode() {
    if (!emailCode.trim()) return;
    setError(null);
    setSubmitting(true);
    try {
      await loginWithCode({ code: emailCode.trim(), email: email.trim() });
      await afterPrivySuccess();
    } catch {
      setError("Code invalide ou expiré.");
      setSubmitting(false);
    }
  }

  async function handleSubmitInvite() {
    if (!pendingAccessToken || !inviteCode.trim()) return;
    await finishWithBackend(pendingAccessToken, inviteCode.trim());
  }

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom"]}>
      <KeyboardAvoidingView style={styles.inner} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.brand}>
          <View style={styles.mark}>
            <Text style={styles.markText}>A</Text>
          </View>
          <Text style={styles.title}>ARIA App</Text>
          <Text style={styles.subtitle}>Canal de secours — accès opérateur</Text>
        </View>

        {step === "choose" && (
          <View style={styles.field}>
            <TouchableOpacity
              style={[styles.button, submitting && styles.buttonDisabled]}
              onPress={() => handleOAuth("google")}
              disabled={submitting}
            >
              <Text style={styles.buttonText}>Continuer avec Google</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.button, submitting && styles.buttonDisabled]}
              onPress={() => handleOAuth("twitter")}
              disabled={submitting}
            >
              <Text style={styles.buttonText}>Continuer avec X</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.buttonOutline, submitting && styles.buttonDisabled]}
              onPress={() => setStep("email_address")}
              disabled={submitting}
            >
              <Text style={styles.buttonOutlineText}>Continuer avec un email</Text>
            </TouchableOpacity>
          </View>
        )}

        {step === "email_address" && (
          <View style={styles.field}>
            <Text style={styles.label}>Adresse email</Text>
            <TextInput
              style={styles.input}
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              placeholder="toi@exemple.com"
              placeholderTextColor={theme.textFaint}
              autoFocus
            />
            <TouchableOpacity
              style={[styles.button, (submitting || !email.trim()) && styles.buttonDisabled]}
              onPress={handleSendEmailCode}
              disabled={submitting || !email.trim()}
            >
              {submitting ? <ActivityIndicator color={theme.accentOn} /> : <Text style={styles.buttonText}>Envoyer le code</Text>}
            </TouchableOpacity>
          </View>
        )}

        {step === "email_code" && (
          <View style={styles.field}>
            <Text style={styles.label}>Code reçu par email</Text>
            <TextInput
              style={styles.input}
              value={emailCode}
              onChangeText={setEmailCode}
              keyboardType="number-pad"
              placeholder="000000"
              placeholderTextColor={theme.textFaint}
              autoFocus
            />
            <TouchableOpacity
              style={[styles.button, (submitting || !emailCode.trim()) && styles.buttonDisabled]}
              onPress={handleVerifyEmailCode}
              disabled={submitting || !emailCode.trim()}
            >
              {submitting ? <ActivityIndicator color={theme.accentOn} /> : <Text style={styles.buttonText}>Confirmer</Text>}
            </TouchableOpacity>
          </View>
        )}

        {step === "invite_code" && (
          <View style={styles.field}>
            <Text style={styles.label}>Première connexion — code d'invitation</Text>
            <Text style={styles.hint}>Génère-le sur Telegram avec /mobileinvite.</Text>
            <TextInput
              style={styles.input}
              value={inviteCode}
              onChangeText={setInviteCode}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Code"
              placeholderTextColor={theme.textFaint}
              autoFocus
            />
            <TouchableOpacity
              style={[styles.button, (submitting || !inviteCode.trim()) && styles.buttonDisabled]}
              onPress={handleSubmitInvite}
              disabled={submitting || !inviteCode.trim()}
            >
              {submitting ? <ActivityIndicator color={theme.accentOn} /> : <Text style={styles.buttonText}>Lier ce compte</Text>}
            </TouchableOpacity>
          </View>
        )}

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <Text style={styles.footnote}>
          Connecté directement à ton VPS — aucune dépendance à Telegram
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  inner: { flex: 1, paddingHorizontal: 26, paddingTop: 24, paddingBottom: 20 },
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
  field: { gap: 12 },
  label: { color: theme.textDim, fontSize: 12, marginBottom: -4 },
  hint: { color: theme.textFaint, fontSize: 11.5, marginBottom: 4 },
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
  button: {
    backgroundColor: theme.accent,
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: "center",
  },
  buttonOutline: {
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.border,
    backgroundColor: theme.surfaceRaised,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: theme.accentOn, fontSize: 15, fontWeight: "600" },
  buttonOutlineText: { color: theme.text, fontSize: 15, fontWeight: "600" },
  error: { color: theme.danger, fontSize: 13, marginTop: 14, textAlign: "center" },
  footnote: {
    marginTop: "auto",
    textAlign: "center",
    color: theme.textFaint,
    fontSize: 11.5,
    lineHeight: 18,
  },
});
