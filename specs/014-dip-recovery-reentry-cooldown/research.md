# Research: dip_recovery_v2_reentry_cooldown

## Decision 1: A second, independent local-DB check in `_maybe_open_position`, run right after the open-position dedup

**Decision**: Add `_recently_closed_via_take_profit(db, contract, chain) -> bool`, querying
`SELECT close_reason, closed_at FROM dip_recovery_v2_shadow WHERE contract=? AND chain=? AND
status='closed' ORDER BY closed_at DESC LIMIT 1`. If a row exists, its `close_reason ==
"take_profit_25pct"`, and `now - closed_at < REENTRY_COOLDOWN_MINUTES`, refuse the candidate.
Placed in `_maybe_open_position` immediately after the existing `_has_open_position` check and
BEFORE the paid `_resolve_market_cap_and_price` (DexScreener) call — same funnel doctrine as
every other filter in this pocket: a free local query rejects cheaply before spending a network
call.

**Rationale**: `ORDER BY closed_at DESC LIMIT 1` always reflects the MOST RECENT close, so an old,
long-resolved close on the same pair never blocks a candidate a much more recent close would
already have cleared (spec's Edge Cases). Running this check on the SAME open `aiosqlite`
connection `_maybe_open_position` already holds costs nothing extra structurally — one more query,
zero new network round-trip.

**Alternatives considered**:
- *Extend `_has_open_position` itself to also check the cooldown.* Rejected: conflates two
  independently-motivated checks (specs/012's "never two simultaneous open positions" vs this
  feature's "don't immediately re-cycle a token") under one function name, making a future reader
  need to re-derive which check does what. Two small, separately-named functions read clearly
  and test independently (FR-007).
- *Add a `last_closed_at`/`cooldown_until` column to the table, updated on every close.* Rejected:
  data-model.md documents this — the existing `close_reason`/`closed_at` columns on the most
  recent row already answer the question with a plain query; a denormalized column would need to
  be kept in sync on every write for a query that already runs in constant time against a small
  per-pocket table with an existing index (`idx_dip_recovery_v2_shadow_lookup` on
  contract/chain/status) — added complexity with no real query-cost problem to solve.

## Decision 2: Cooldown applies ONLY to `take_profit_25pct` closes, never `timeout_max_hold`

**Decision**: `_recently_closed_via_take_profit`'s query only matches `close_reason ==
"take_profit_25pct"` (verified live in `dip_recovery_v2_shadow.py`: the two close-reason string
literals in use today are exactly `"take_profit_25pct"` and `"timeout_max_hold"`, confirmed by
reading `_advance_one_position`). A timeout close never triggers this cooldown.

**Rationale**: The real incident's failure shape is specific: a token closes profitably NEAR ITS
OWN RECENT PRICE RANGE and can immediately re-qualify on `var_24h_pct` measurement noise rather
than a genuine new dip. A timeout close means `MAX_HOLD_HOURS=168.0` (7 days) already elapsed with
the position never reaching +25% — a fundamentally different, already long-cooled market
situation. Adding this cooldown on top of a timeout would only suppress a legitimate new entry on
a token that happens to re-qualify after a genuinely long gap, with no corresponding protection
against the actual risk this feature targets.

**Alternatives considered**:
- *Apply the cooldown to every close reason uniformly.* Rejected: spec's User Story 2 explicitly
  requires an examined, non-default choice here — uniform application would be the "silently
  assuming symmetry" the spec warns against, and provides no benefit for the timeout case (see
  Rationale above).

## Decision 3: `REENTRY_COOLDOWN_MINUTES = 60`, a conservative placeholder pending real data

**Decision**: `REENTRY_COOLDOWN_MINUTES = 60` (1 hour).

**Rationale**: The real incident (a 15-minute gap, price barely moved) shows 15 minutes is clearly
too short. No empirical distribution exists yet for how long DexPaprika's `var_24h_pct`
instability typically persists after a close — same "no data yet, conservative placeholder,
RECALIBRATE later" posture already used for `ENTRY_SANITY_MIN_CONFLICT_PCT=10.0` (specs/013) and
`EXIT_PRICE_SANITY_MULTIPLE=50.0` (specs/012) in this same pocket. 60 minutes is a round,
conservative-but-not-extreme starting point: comfortably long enough that the exact incident
(15 minutes) would have been blocked, short enough that a genuinely new dip hours later is never
suppressed by this guard alone (the entry-sanity guard and market-cap/liquidity/pool-age filters
still apply independently on any later attempt). RECALIBRATE once n>=100 real
candidates blocked/passed by this guard accumulate (Doctrine d'Ingestion: a placeholder value is
always stated as such, never presented as final).

**Alternatives considered**:
- *A much longer cooldown (e.g. 24h).* Rejected for now: no data yet justifies a value this
  restrictive, and it would risk suppressing legitimate re-entries for a full day on a pocket
  whose entire test criterion (specs/012 SC-005) needs volume to reach a meaningful sample size.
  A shorter, revisable starting point is more aligned with this dome's "prove it needs tightening
  before tightening it" posture.
- *No fixed duration — a dynamic cooldown scaled to `MAX_HOLD_HOURS` or the previous position's
  hold duration.* Rejected: adds real complexity (a formula to justify and test) for a threshold
  that has zero empirical basis to calibrate a formula against yet — a flat, documented placeholder
  is the honest choice until real data exists (same reasoning already applied to every other
  threshold in this pocket).

## Decision 4: Rejection logged via the module's existing `logger.info` convention

**Decision**: On rejection, emit `logger.info("dip_recovery_v2_shadow: reentry cooldown rejected
%s (closed_at=%s, take_profit_25pct, %.1f min ago)", contract, closed_at.isoformat(),
minutes_elapsed)` — same style already used for the specs/013 entry-sanity guard and this
module's other `logger.info` lines.

**Rationale**: Same reasoning as specs/013 Decision 3 — this pocket has no
`pretrade_rejection_log` wiring, and a distinguishable log line satisfies FR-005 without importing
a heavier mechanism built for a different pipeline.

## Decision 5: Aggregate stats reporting is NOT retroactively changed by this feature

**Decision**: `_dip_v2_aggregate()` (the function feeding the Telegram "Cumul"/"Debit 1h" lines) is
left unchanged by this feature.

**Rationale**: Spec's Assumptions explicitly scope this out — distinguishing "N independent
tokens traded" from "N trade cycles, some clustered on the same token" in the aggregate reporting
is a real, separate concern, flagged as a candidate future improvement rather than folded into
this cooldown fix (avoiding scope creep per the spec's own instruction).
