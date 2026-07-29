import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { fetchSession, logout } from "../api/auth";
import { newIdempotencyKey, sendChatMessage } from "../api/chat";
import { ApiError, NetworkError } from "../api/client";
import { theme } from "../theme";

interface Message {
  id: string;
  from: "operator" | "aria";
  text: string;
  at: number;
}

type ConnectionState = "online" | "offline";

export function ChatScreen({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("online");
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<FlatList<Message>>(null);

  useEffect(() => {
    // Confirms the session is actually still valid at screen mount -- an
    // expired/revoked session (e.g. from another device) sends the operator
    // straight back to login rather than showing a chat that will fail silently.
    fetchSession().catch(async (err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        await logout();
        onLoggedOut();
      }
    });
  }, [onLoggedOut]);

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || thinking) return;
    setDraft("");
    setConnection("online");

    const mine: Message = { id: newIdempotencyKey(), from: "operator", text, at: Date.now() };
    setMessages((prev) => [...prev, mine]);
    setThinking(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const reply = await sendChatMessage(text, mine.id, controller.signal);
      setMessages((prev) => [
        ...prev,
        { id: `${mine.id}-reply`, from: "aria", text: reply.reply, at: Date.now() },
      ]);
    } catch (err) {
      if (controller.signal.aborted) {
        // Client-side cancellation only -- the server call may still finish and
        // log its own response; we simply stop waiting on it here.
      } else if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        await logout();
        onLoggedOut();
      } else if (err instanceof NetworkError) {
        setConnection("offline");
      } else {
        setMessages((prev) => [
          ...prev,
          { id: `${mine.id}-error`, from: "aria", text: "Erreur — réessaie dans un instant.", at: Date.now() },
        ]);
      }
    } finally {
      setThinking(false);
      abortRef.current = null;
    }
  }, [draft, thinking, onLoggedOut]);

  function handleCancel() {
    abortRef.current?.abort();
  }

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.header}>
        <View style={styles.headerId}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>A</Text>
          </View>
          <View>
            <Text style={styles.name}>ARIA</Text>
            <View style={styles.sub}>
              <View style={[styles.pulse, connection === "offline" && styles.pulseOffline]} />
              <Text style={styles.subText}>{connection === "online" ? "en ligne" : "hors ligne"}</Text>
            </View>
          </View>
        </View>
        <TouchableOpacity
          onPress={async () => {
            await logout();
            onLoggedOut();
          }}
        >
          <Text style={styles.logout}>Déconnexion</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        ref={listRef}
        style={styles.body}
        contentContainerStyle={styles.bodyContent}
        data={messages}
        keyExtractor={(m) => m.id}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        renderItem={({ item }) => (
          <View style={[styles.bubbleRow, item.from === "operator" && styles.bubbleRowMine]}>
            <View style={[styles.bubble, item.from === "operator" ? styles.bubbleMine : styles.bubbleTheirs]}>
              <Text style={item.from === "operator" ? styles.bubbleTextMine : styles.bubbleText}>
                {item.text}
              </Text>
            </View>
          </View>
        )}
        ListFooterComponent={
          thinking ? (
            <TouchableOpacity style={styles.thinkingRow} onPress={handleCancel}>
              <Text style={styles.thinkingText}>ARIA réfléchit — toucher pour annuler</Text>
            </TouchableOpacity>
          ) : null
        }
      />

      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Écrire à ARIA…"
          placeholderTextColor={theme.textFaint}
          multiline
        />
        <TouchableOpacity
          style={[styles.sendButton, (!draft.trim() || thinking) && styles.sendButtonDisabled]}
          onPress={handleSend}
          disabled={!draft.trim() || thinking}
        >
          <Text style={styles.sendButtonText}>↑</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: 58,
    paddingHorizontal: 20,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: theme.borderSoft,
  },
  headerId: { flexDirection: "row", alignItems: "center", gap: 10 },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: 11,
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.border,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 10,
  },
  avatarText: { color: theme.accent, fontSize: 16, fontWeight: "600" },
  name: { color: theme.text, fontSize: 14.5, fontWeight: "600" },
  sub: { flexDirection: "row", alignItems: "center", marginTop: 2 },
  pulse: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: theme.success,
    marginRight: 5,
  },
  pulseOffline: { backgroundColor: theme.textFaint },
  subText: { color: theme.textFaint, fontSize: 11 },
  logout: { color: theme.textDim, fontSize: 12.5 },
  body: { flex: 1 },
  bodyContent: { padding: 16, gap: 10 },
  bubbleRow: { flexDirection: "row" },
  bubbleRowMine: { justifyContent: "flex-end" },
  bubble: { maxWidth: "78%", paddingVertical: 10, paddingHorizontal: 13, borderRadius: 16 },
  bubbleMine: { backgroundColor: theme.accent, borderBottomRightRadius: 5 },
  bubbleTheirs: {
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.borderSoft,
    borderBottomLeftRadius: 5,
  },
  bubbleText: { color: theme.text, fontSize: 13.5, lineHeight: 19 },
  bubbleTextMine: { color: theme.accentOn, fontSize: 13.5, lineHeight: 19, fontWeight: "500" },
  thinkingRow: {
    alignSelf: "flex-start",
    backgroundColor: theme.surfaceRaised,
    borderWidth: 1,
    borderColor: theme.borderSoft,
    borderRadius: 16,
    borderBottomLeftRadius: 5,
    paddingVertical: 10,
    paddingHorizontal: 13,
    marginTop: 4,
  },
  thinkingText: { color: theme.textDim, fontSize: 12 },
  inputBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    padding: 12,
    borderTopWidth: 1,
    borderTopColor: theme.borderSoft,
  },
  input: {
    flex: 1,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 11,
    color: theme.text,
    fontSize: 13.5,
    maxHeight: 120,
  },
  sendButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: theme.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  sendButtonDisabled: { opacity: 0.4 },
  sendButtonText: { color: theme.accentOn, fontSize: 16, fontWeight: "600" },
});
