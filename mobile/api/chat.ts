import * as Crypto from "expo-crypto";
import { apiClient } from "./client";

export interface ChatReply {
  reply: string;
  skill_used?: string | null;
  actions_taken?: string[];
  data?: Record<string, unknown>;
}

/** One idempotency key per logical message (plan requirement) -- a client-side
 * timeout followed by a server-side success must never re-trigger a second,
 * duplicate AriaBrain.process() call for the same message. */
export function newIdempotencyKey(): string {
  return Crypto.randomUUID();
}

export function sendChatMessage(
  message: string,
  idempotencyKey: string,
  signal: AbortSignal,
): Promise<ChatReply> {
  return apiClient.postCancellable<ChatReply>(
    "/api/aria/ops/chat",
    { message, idempotency_key: idempotencyKey },
    signal,
  );
}
