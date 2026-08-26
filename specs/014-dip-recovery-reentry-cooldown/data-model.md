# Data Model: dip_recovery_v2_reentry_cooldown

## No schema change

This feature queries the `dip_recovery_v2_shadow` table's own existing `close_reason` and
`closed_at` columns — no new column, no new table. Same non-goal posture as specs/013.

## Entities

### Most recent close per (contract, chain) (transient, not persisted separately)

Computed on demand inside `_maybe_open_position` via
`SELECT close_reason, closed_at FROM dip_recovery_v2_shadow WHERE contract=? AND chain=? AND
status='closed' ORDER BY closed_at DESC LIMIT 1` — the existing
`idx_dip_recovery_v2_shadow_lookup` index (contract, chain, status) already covers this query.

| Field | Source | Notes |
|---|---|---|
| `close_reason` | `dip_recovery_v2_shadow.close_reason` | Only `"take_profit_25pct"` triggers the cooldown (research.md Decision 2); `"timeout_max_hold"` never does. |
| `closed_at` | `dip_recovery_v2_shadow.closed_at` | Compared against `now` to compute elapsed minutes. |

**Validation rule** (FR-002): refuse the candidate when a most-recent-close row exists, its
`close_reason == "take_profit_25pct"`, and `(now - closed_at).total_seconds() / 60.0 <
REENTRY_COOLDOWN_MINUTES` (60). No row (contract never closed before) never refuses (FR-003).

### Cooldown rejection reason (log line, not persisted)

A `logger.info` line distinguishing this guard's rejection from every other rejection reason in
this module (research.md Decision 4) — not a new persisted entity.

## Non-goals

- No new column on `dip_recovery_v2_shadow`.
- No new table.
- No change to `_has_open_position` (specs/012) or the entry-sanity guard (specs/013,
  `ENTRY_SANITY_MIN_CONFLICT_PCT`).
- No retroactive change to `_dip_v2_aggregate()`'s win-rate/PnL reporting (research.md Decision 5).
