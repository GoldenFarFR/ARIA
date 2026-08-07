import React, { useCallback, useEffect, useState } from "react";
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
import { armStop, getPauseStatus, liftStop, type PauseStatus } from "../api/stop";
import { ApiError, NetworkError } from "../api/client";
import { theme } from "../theme";

// Dedicated kill-switch screen (07/08, closes the gap noted in
// HANDOFF_OPERATOR_MOBILE.md: "a real STOP button in the app" was open
// since Item #201 Phase 2). Two-step by design -- one tap reveals the TOTP
// field rather than firing /stop immediately, so the single most consequential
// control in this app can't be triggered by a stray tap. The backend's own
// _require_fresh_totp is the real second factor; this UI step is just a
// confirmation guard on top of it.
export function StopScreen() {
  const [status, setStatus] = useState<PauseStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoadingStatus(true);
    try {
      setStatus(await getPauseStatus());
    } catch {
      // best-effort -- the screen still renders, just without a known state
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function submit() {
    if (submitting || totpCode.trim().length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const action = status?.paused ? liftStop : armStop;
      const result = await action(totpCode.trim());
      setStatus(result);
      setTotpCode("");
      setConfirming(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("Code invalide ou déjà utilisé — attends le prochain code.");
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Trop de tentatives — patiente un instant avant de réessayer.");
      } else if (err instanceof NetworkError) {
        setError("Hors ligne — impossible de joindre le serveur.");
      } else {
        setError("Action impossible pour le moment.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const isPaused = status?.paused === true;

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      {loadingStatus ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.accent} />
        </View>
      ) : (
        <>
          <View style={[styles.statusCard, isPaused ? styles.statusPaused : styles.statusActive]}>
            <Text style={styles.statusLabel}>{isPaused ? "ARIA EN PAUSE" : "ARIA ACTIVE"}</Text>
            {isPaused && status?.since && (
              <Text style={styles.statusDetail}>Depuis {new Date(status.since).toLocaleString("fr-FR")}</Text>
            )}
            {isPaused && status?.reason && <Text style={styles.statusDetail}>{status.reason}</Text>}
            {isPaused && status?.by && <Text style={styles.statusDetail}>Déclenché par {status.by}</Text>}
          </View>

          {!confirming ? (
            <TouchableOpacity
              style={[styles.actionButton, isPaused ? styles.resumeButton : styles.stopButton]}
              onPress={() => setConfirming(true)}
              activeOpacity={0.8}
            >
              <Text style={styles.actionText}>{isPaused ? "Reprendre ARIA" : "Arrêter ARIA"}</Text>
            </TouchableOpacity>
          ) : (
            <View style={styles.confirmBlock}>
              <Text style={styles.confirmLabel}>
                Code de ton authenticator pour {isPaused ? "reprendre" : "arrêter"} ARIA
              </Text>
              <TextInput
                style={styles.input}
                value={totpCode}
                onChangeText={setTotpCode}
                keyboardType="number-pad"
                autoFocus
                maxLength={10}
                placeholder="000000"
                placeholderTextColor={theme.textFaint}
              />
              {error ? <Text style={styles.error}>{error}</Text> : null}
              <View style={styles.confirmRow}>
                <TouchableOpacity
                  style={styles.cancelButton}
                  onPress={() => {
                    setConfirming(false);
                    setTotpCode("");
                    setError(null);
                  }}
                >
                  <Text style={styles.cancelText}>Annuler</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[
                    styles.actionButton,
                    styles.confirmButton,
                    isPaused ? styles.resumeButton : styles.stopButton,
                    (submitting || totpCode.trim().length === 0) && styles.buttonDisabled,
                  ]}
                  onPress={submit}
                  disabled={submitting || totpCode.trim().length === 0}
                >
                  {submitting ? (
                    <ActivityIndicator color={theme.accentOn} />
                  ) : (
                    <Text style={styles.actionText}>Confirmer</Text>
                  )}
                </TouchableOpacity>
              </View>
            </View>
          )}

          <Text style={styles.footnote}>
            Coupe/reprend la boucle de trading en direct — le kill-switch Telegram (/stop) reste
            disponible dans tous les cas.
          </Text>
        </>
      )}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg, padding: 20 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  statusCard: {
    borderRadius: 16,
    borderWidth: 1,
    padding: 18,
    marginBottom: 24,
    gap: 4,
  },
  statusActive: { backgroundColor: "rgba(74,222,128,0.08)", borderColor: theme.success },
  statusPaused: { backgroundColor: "rgba(229,72,77,0.08)", borderColor: theme.danger },
  statusLabel: { color: theme.text, fontSize: 15, fontWeight: "700", letterSpacing: 0.03 },
  statusDetail: { color: theme.textDim, fontSize: 12.5 },
  actionButton: {
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  stopButton: { backgroundColor: theme.danger },
  resumeButton: { backgroundColor: theme.success },
  actionText: { color: "#fff", fontSize: 15.5, fontWeight: "700" },
  buttonDisabled: { opacity: 0.5 },
  confirmBlock: { gap: 12 },
  confirmLabel: { color: theme.textDim, fontSize: 13, textAlign: "center" },
  input: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 13,
    color: theme.text,
    fontSize: 20,
    textAlign: "center",
    letterSpacing: 4,
  },
  error: { color: theme.danger, fontSize: 12.5, textAlign: "center" },
  confirmRow: { flexDirection: "row", gap: 10 },
  cancelButton: {
    flex: 1,
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
  },
  cancelText: { color: theme.textDim, fontSize: 14.5, fontWeight: "600" },
  confirmButton: { flex: 1 },
  footnote: {
    marginTop: "auto",
    textAlign: "center",
    color: theme.textFaint,
    fontSize: 11.5,
    lineHeight: 17,
  },
});
