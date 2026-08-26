# Research: Dip-recovery shadow pocket, v2

## Decision 1: Dedup on open-position existence, not a recovery-triggered episode flag

**Decision**: Replace the draft's `dip_recovery_v2_shadow_episode_state` table (a per-
(contract, chain) `in_episode` flag, copied from v1's pattern) with a direct check: before
opening a position, query whether a `status='open'` row already exists in
`dip_recovery_v2_shadow` for that (contract, chain). If yes, skip. If no, and every entry filter
passes, open a new position. No episode-state table at all.

**Rationale**: v1 (`dip_recovery_shadow.py`) evaluates every watchlist token every cycle via
`record_evaluation`, recovered or not — so its episode flag reliably observes the transition back
above threshold and clears itself. v2 sources candidates from
`dexpaprika.get_trending_pools(chain, order_by="price_change_percentage_24h", sort="asc", ...)`,
which only ever returns the chain's current worst-24h performers. A token that recovers above
-30% simply stops appearing in that feed — v2 never observes the recovery, so a recovery-
triggered flag can only ever transition to `True` and never back to `False`. This was not a
hypothetical risk: it was caught live this session by
`test_discover_rearms_after_recovery_above_threshold`, which failed against the original draft.
The open-position check sidesteps the problem entirely — it only asks "is one currently open",
which is directly knowable from `dip_recovery_v2_shadow` itself, with no dependency on ever
observing a recovery event.

**Alternatives considered**:
- *Keep the episode-state table, but also poll DexScreener directly for previously-dipping
  tokens to detect recovery.* Rejected: this reintroduces an unbounded, per-known-token polling
  loop — exactly the "linear/unbounded resource pattern" the funnel doctrine forbids, to solve a
  problem the open-position check solves for free.
- *Drop dedup entirely, rely on the take-profit/timeout close to naturally end each position.*
  Rejected: without a check, the SAME discovery pass could see the same still-dipping token on
  a later cycle and open a second position on it before the first has even closed — this is
  exactly what dedup exists to prevent (FR-006).

## Decision 2: Add a price-sanity guard on the exit check (FR-010a)

**Decision**: Yes — add a guard mirroring `PEAK_PRICE_SANITY_MULTIPLE`, added earlier the same
day (2026-08-26) to `base_momentum_shadow.py` after a real incident (id=202, a corrupted AMM
reserve-ratio price read as "+707006.8% nominal, never executable"). Concretely: before treating
a fresh DexScreener quote as a valid take-profit close, reject/skip the check if the implied
price ratio versus `entry_price` is implausible (same order of magnitude as the base_momentum
guard — a multiplier far beyond anything a genuine +25% target requires).

**Rationale**: this pocket's exit check reads a fresh `dexscreener.fetch_token_pairs()` quote on
every pass, from the exact same provider whose corrupted read caused a real, confirmed incident
in a sibling pocket earlier the same day. The failure mode (an AMM pool whose reserves have
partially collapsed, producing a nonsensical implied price) is a property of the provider/chain
data, not of `base_momentum_shadow.py`'s own code — nothing about v2's setup makes it immune. The
guard is a single `if` before honoring a take-profit close; implementation cost is trivial
against a confirmed, same-day, same-provider risk.

**Alternatives considered**:
- *Do nothing, treat it as low-probability.* Rejected: the risk was not hypothetical, it happened
  in production the same day on a pocket using the same price provider.
- *Cross-check against a second provider (DexPaprika) before honoring a close.* Rejected for now
  as unnecessary complexity: a same-provider sanity bound (implausible ratio vs. entry) catches
  the specific failure mode already observed, at a fraction of the cost (no second network call
  per exit check). Revisit only if a false-positive take-profit is later observed in this
  pocket's own data.

## Decision 3: 14-day pair-age filter via `TrendingPool.pool_created_at`, cost-free

**Decision**: Use `TrendingPool.pool_created_at` (already populated by `dexpaprika.py`'s
`get_trending_pools`, itself reusing `geckoterminal.TrendingPool` — confirmed by reading
`dexpaprika.py`'s pool-construction code, which already carries this field from the raw API
response with zero extra network call). Apply the filter inside `discover_and_record`'s
candidate loop, alongside the existing `var_24h` check, BEFORE the paid DexScreener call — same
funnel-doctrine placement as every other free filter. A candidate with `pool_created_at is None`
(unavailable/unparseable) is treated as not qualifying — never fabricated as "old enough" by
default, same never-fabricate dome doctrine as every other guard in this pocket.

**Rationale**: the field already exists in the response ARIA already fetches for its own -30%/
24h screening; there is no reason to add a second call or a new dependency for this filter.
Placing it before the DexScreener call keeps every paid call bounded to genuinely promising
candidates (funnel doctrine), consistent with FR-003.

**Alternatives considered**:
- *Resolve pool age via a separate on-chain call (e.g. Blockscout contract-creation timestamp).*
  Rejected: strictly more expensive for data DexPaprika already provides for free in the same
  response this pocket already fetches.

## Decision 4: No legitimacy/GoPlus pre-check for this iteration (FR-010b)

**Decision**: No — do not add a GoPlus or other legitimacy pre-check to this pocket's entry path
for this iteration.

**Rationale**: the operator's own framing was explicit ("un truc simpliste pour tester") and this
pocket is shadow-only — zero real capital is ever at risk regardless of a token's legitimacy.
Adding a legitimacy gate now would filter the very sample this test exists to observe (does
"buy the -30% dip, sell +25%" work at all, including on tokens a legitimacy screen might reject)
without the operator having asked for it. This is an explicit, recorded "not now" rather than a
silent omission — revisit only if this pocket is ever considered for graduation beyond shadow.

**Alternatives considered**:
- *Add it anyway "for consistency with the dome's legitimacy-engine doctrine."* Rejected: that
  doctrine exists to protect real capital; applying it reflexively to a zero-capital diagnostic
  test would narrow the very data this pocket is meant to gather, contradicting the operator's
  own "keep it simple" instruction.

## Decision 5: DexPaprika tier — confirmed FREE, 15s staleness note applies (FR-010c)

**Decision**: ARIA's own `dexpaprika.py` client is confirmed, directly from its own source
comment, to use the free tier: `_auth_headers()`'s docstring (04/08) reads "optional free-tier
key (DEXPAPRIKA_API_KEY)... Keyless calls remain the default (this tier works without one)." No
Pro-tier code path exists anywhere in the module. Combined with the live-verified docs
(docs.dexpaprika.com/introduction, fetched this session): free tier serves data "with a delay of
up to 15 seconds" reflecting the latest indexed blocks/events.

**Rationale for "no action needed"**: this pocket's entry signal is a 24h-window price change: a
15-second data lag changes the measured `var_24h` value by a negligible fraction of the -30%
threshold's own precision, and cannot flip a token from "qualifies" to "doesn't qualify" in any
practical scenario. No change to this spec's requirements follows from this confirmation — it is
recorded here specifically so no future session re-derives or misremembers it as "indeterminable
from the code" (it was determinable, and now is determined).

**Alternatives considered**:
- *Upgrade to DexPaprika Pro to remove the 15s lag.* Rejected: no measurable benefit for a
  24h-window signal, real recurring cost for a shadow/simulation-only pocket — revisit only if a
  future pocket needs sub-second pricing, which this one explicitly does not.

## Decision 6: `DISCOVERY_LIMIT=20` confirmed adequate (FR-010d)

**Decision**: keep `DISCOVERY_LIMIT=20` candidates per chain per pass, unchanged from the draft.

**Rationale**: the funnel doctrine is already satisfied — DexPaprika's own `min_liquidity_usd`
server-side filter plus the worst-24h-performer ordering already narrow the population before
this pocket ever sees it; the paid DexScreener call is bounded to at most 20 survivors per chain
per pass (40 total across both chains), a small, fixed, non-growing cost regardless of how many
tokens exist market-wide. No incident or measured cost problem motivates a change.

**Alternatives considered**:
- *Raise the limit to widen the candidate pool.* Rejected for this iteration: no evidence the
  current limit is missing qualifying candidates; raising it before observing real data would be
  an unmeasured guess, contrary to the project's "verify before affirming" doctrine. Revisit once
  SC-004's ≥100-closure review runs, if the sample turns out to be thin.
