# AGENTS.md — ARIA context (root)

> **`CLAUDE.md` is the single source of truth** for this repo — rules, active
> state, architecture, HANDOFF index, everything. This file exists only
> because `AGENTS.md` is a cross-tool standard (read by agents other than
> Claude Code) and CI checks it stays present and leak-free. Read `CLAUDE.md`
> first; this is a short pointer + the few conventions that live only here.

## Absolute rules (summary — full detail in CLAUDE.md)
- GoldenFarFR makes all final decisions on important matters.
- Never execute a real-capital trade without human validation, except the
  named/bounded exceptions in CLAUDE.md's "Règles absolues" (paper-trading,
  the 10-25$ agent-wallet pilot, the bounded USDC transfer).
- Never modify guardrail files (`permission_mode`, `wallet_guard`,
  `regles-uniques`, `config.toml`) or your own code without explicit "ok".
- Reason only on verifiable facts; verify before asserting, even something
  CLAUDE.md itself already states — it can be stale.
- Method: Analyze → propose a plan → wait for explicit "go"/"ok" → implement
  → journal → honest self-critique.

## Generic external-client error policy
Referenced by `services/blockscout.py`, `services/coingecko.py`, and other
read-only external clients as "the AGENTS.md error policy":
- 429: exponential backoff, 3 attempts max, then give up without blocking
  the pipeline.
- Timeout / endpoint unavailable: 1 retry after 5s, then an explicit
  fallback.
- Missing data is never replaced by a guess — an `error` field (and
  `available=False`) carries the absence of data.
- Repeated consecutive failures (≥3): logged, and a temporary circuit
  breaker opens — every call skips straight to graceful degradation during
  the cooldown, never blocking the pipeline, never spamming Telegram.

## Everything else
Architecture, active gates, deployment, HANDOFF index, VC/momentum theses,
operator profile, model policy — see `CLAUDE.md`.
