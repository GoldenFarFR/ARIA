# Quickstart: dip_recovery_v2_reentry_cooldown

## Prerequisites

- `packages/aria-core/.venv`. No new environment variables, no new gate.

## Validation checks

### 1. Test suite

```bash
cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py tests/test_coherence.py -q -n auto
```

### 2. The real incident, reproduced (SC-001)

Position id=15 (contract via pool `0x49a11a3515755a730b20ae1d6c3ef5a997e20f728ad46d8859654c4d4eaad95a`,
chain=robinhood, symbol EARTHCOIN) closed `take_profit_25pct` 15 minutes before a new qualifying
candidate for the same contract arrives. Feeding this exact shape into `_maybe_open_position`
must result in NO new position opened and a distinguishable log line ("reentry cooldown").

### 3. Cooldown expires normally (SC-002)

A candidate for a contract whose most recent close (`take_profit_25pct`) happened 2 hours ago
(past `REENTRY_COOLDOWN_MINUTES=60`) — a position opens exactly as it would have before this
feature.

### 4. Timeout closes are never subject to the cooldown (research.md Decision 2)

A candidate for a contract whose most recent close was `timeout_max_hold` 5 minutes ago (well
within the 60-minute window) — a position STILL opens, since the cooldown only applies to
`take_profit_25pct` closes.

### 5. A contract with no prior close is unaffected (FR-003)

A brand-new contract with no rows in `dip_recovery_v2_shadow` at all — the cooldown check finds
no row and never refuses.

### 6. Zero new network calls (SC-004)

Confirm the cooldown check is a pure local SQLite query on the already-open connection
`_maybe_open_position` holds — no new call site to `dexpaprika`/`dexscreener`.

### 7. Rejection is distinguishable (FR-005)

Confirm the log line emitted on a cooldown rejection contains "reentry cooldown", distinct from
the specs/013 entry-sanity guard's "entry sanity guard" line and this module's other rejection
reasons.
