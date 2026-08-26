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

## Decision 7: Wire `shadow_candle_archive` (before + after), operator-added mid-implementation

**Decision**: Yes -- archive the real OHLCV candle path for every position, both the candles
that justified entry (`phase="before"`, one `dexpaprika.get_ohlcv` call right after a position
opens) and the candles observed on every exit-tracking pass while a position stays open
(`phase="after"`, one call per open position per `advance_open_positions` pass). Same shared
table (`shadow_candle_archive`, `module="dip_recovery_v2"`) and API every other shadow module in
the dome already uses.

**Rationale**: raised by the operator mid-implementation, independent of the FR-010 list: "même
si on calibre ça au hasard" (even if today's entry/exit parameters turn out to be an arbitrary
first guess), the real price path of every open position must be on disk so an alternate
strategy (a different take-profit level, a trailing stop instead of a fixed one, a different
market-cap band) can be honestly re-simulated against real data later -- exactly the standing
18/08 dome-wide convention ("je veut les bougies avant et apres le point dachat a chaque futur
shadow") this pocket had not yet been wired to. Without it, a future recalibration would face the
exact gap `shadow_candle_archive.py`'s own docstring describes: only entry/peak/exit snapshots on
disk, no path in between, forcing a live re-fetch that may not even be possible after the fact.

**Cost, stated plainly rather than hidden**: this adds one real network call (`dexpaprika.get_ohlcv`)
per newly-opened position (once) and one more per open position per pass (recurring, bounded by
however many positions are open at once -- not unbounded, same shape as the pricing call this
pocket already makes each pass). Accepted explicitly by the operator's own request; not something
this plan would have added unprompted given the funnel-doctrine's default bias toward fewer calls.

**Alternatives considered**:
- *Archive only "after" candles, skip "before".* Rejected: the "before" candles are exactly what
  justified the entry signal -- omitting them would leave the same class of gap this convention
  exists to close, just shifted to the entry side instead of the exit side.
- *Reuse the DexScreener spot-price call already made for take-profit/timeout evaluation instead
  of a second DexPaprika OHLCV call.* Rejected: a single spot price is not a candle series --
  re-simulating an alternate exit rule needs the intra-position price PATH, which only an OHLCV
  call provides; the two calls serve genuinely different purposes.

## Decision 8: Telegram open/close notifications, own format rather than reusing `shadow_notify`

**Decision**: Build a dedicated notification path inside `dip_recovery_v2_shadow.py`
(`pending_notifications()`) rather than reusing `shadow_notify.notify_pocket()`. Same visual
shape (OUVERTURE/CLOTURE, DexScreener link, a rolling aggregate) as every other shadow pocket the
operator already sees notifications for, but built against THIS pocket's own real fields (market
cap, liquidity, 24h dip, pool age, fixed take-profit/timeout) rather than the scale-out-ladder
fields (`SCALE_OUT_STEP_PCT`, `next_scale_level`, m5/m15 surge data) `shadow_notify.py` expects.
Wired into `heartbeat.py`'s existing `_notify_telegram` (the same Telegram send path 20+ other
heartbeat tasks already use), called right after `dip_recovery_v2_shadow_cycle`'s dispatch.

**Rationale**: raised by the operator ("je veux toutes les meme notif a l'identique sur telegram
achat et vente") after the deploy. `shadow_notify.notify_pocket()` cannot be reused directly for
two structural reasons: (1) it is called from `shadow_persistent.py`, the standalone OUT-OF-REPO
process that runs Base/Robinhood/Solana's OWN shadow pockets -- this pocket runs in-process via
`heartbeat.py` instead (same deployment shape as v1, `dip_recovery_shadow.py`), never in that
process; (2) even if called from the right process, its message template reads fields
(`SCALE_OUT_STEP_PCT`, `m5_pct`, `buyers_m15`, `next_scale_level`) this pocket's schema simply
does not have -- a fixed take-profit/timeout pocket has no scale-out ladder to describe. Building
a dedicated, schema-correct template is safer than bending a shared one until it silently reads
`None` for fields that will never exist here.

**Alternatives considered**:
- *Migrate this pocket to `shadow_persistent.py` so it can reuse `shadow_notify.notify_pocket()`
  verbatim.* Rejected: architecturally heavier (out-of-repo file, `systemctl restart` to deploy),
  inconsistent with v1's own precedent (`dip_recovery_shadow.py` stays in-process), and the
  scale-out-shaped template still would not fit this pocket's fields regardless of which process
  calls it.
- *Generalize `shadow_notify.py` to also support a fixed-take-profit shape.* Rejected for now:
  `shadow_notify.py`'s own docstring already documents a past incident where a premature shared
  abstraction silently rendered a wrong value for a pocket whose parameters had diverged from its
  siblings -- the same risk applies here (a fixed-take-profit pocket has structurally different
  fields, not just different numbers). A dedicated, explicit template is the safer choice the
  same doctrine that docstring describes would recommend.
