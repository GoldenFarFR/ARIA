# Data Model: dip_recovery_v2_entry_sanity_guard

## No schema change

This feature is a pure decision-time evaluation step inside `_maybe_open_position` — it does not
persist any new field on the `dip_recovery_v2_shadow` table and does not introduce a new table.
The DexScreener `price_change_24h` reading used for the cross-check is read from the already-
fetched `PairSnapshot` object at decision time and discarded once the entry/reject decision is
made (same lifecycle as `snapshot.market_cap_usd`/`snapshot.liquidity_usd`, which are also read
and used without being persisted verbatim beyond the columns the table already has).

## Entities

### Entry candidate cross-check (transient, not persisted)

A pairing of two independently-sourced 24h-change readings for the same (contract, chain)
candidate, evaluated once per candidate inside `_maybe_open_position`, after
`_resolve_market_cap_and_price` has already resolved a `PairSnapshot`:

| Field | Source | Notes |
|---|---|---|
| `var_24h_pct` | `dexpaprika.get_trending_pools` (discovery time) | Already a `_maybe_open_position` parameter today. |
| `price_change_24h` | `dexscreener.PairSnapshot` (resolution time, same call already made for market cap/liquidity) | New read of an existing field — zero extra network cost. |

**Validation rule** (FR-002, research.md Decision 1): reject the candidate when
`var_24h_pct <= DIP_THRESHOLD_PCT` (i.e. the entry bar is already met) AND
`price_change_24h >= ENTRY_SANITY_MIN_CONFLICT_PCT` (a new constant, `10.0`). No other
combination rejects.

### Rejection reason (log line, not persisted)

A `logger.info` line distinguishing this guard's rejection from every other rejection reason in
this module (research.md Decision 3) — not a new persisted entity, consistent with this module's
existing minimal-logging convention.

## Non-goals

- No new column on `dip_recovery_v2_shadow`.
- No new table.
- No change to `dexscreener.PairSnapshot`'s shape (research.md Decision 2: the existing
  0.0-default is safe under this guard's rule, no new sentinel needed).
