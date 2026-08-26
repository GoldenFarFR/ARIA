# Research: dip_recovery_v2_entry_sanity_guard

## Decision 1: Reject only on a sign disagreement where both readings are large in magnitude

**Decision**: Add a constant `ENTRY_SANITY_MIN_CONFLICT_PCT = 10.0`. Reject the entry when
`var_24h_pct <= DIP_THRESHOLD_PCT` (the existing entry bar, -30.0) AND
`snapshot.price_change_24h >= ENTRY_SANITY_MIN_CONFLICT_PCT` (a large POSITIVE DexScreener
reading for the same candidate). Any other combination — including a DexScreener reading that is
itself negative but smaller in magnitude, exactly zero, or only mildly positive (below the
10.0 floor) — is treated as ordinary provider drift and does not block the entry.

**Rationale**: The real incident (position id=13, contract `0x23acfab04106a21af0ae1643b74cfec3c9aac181`,
chain=robinhood) was a full sign flip: DexPaprika read -31.9487% at entry (2026-08-26T20:08:51
UTC), DexScreener's own live card and DexPaprika's own live chatbot answer both independently
agreed on ~+29% minutes later for the same token. A guard aimed at catching this class of failure
should trigger on that exact shape — one provider says "big dip", the other says "big gain" —
not on small numeric disagreement, which is expected any time two providers sample at slightly
different instants (DexPaprika's discovery call happens before DexScreener's resolution call in
the same `_maybe_open_position` pass).

**Alternatives considered**:
- *A percentage-delta threshold between the two readings* (e.g. reject if
  `|var_24h_pct - price_change_24h| > N`). Rejected: since the entry criterion already requires
  `var_24h_pct <= -30.0`, virtually any DexScreener reading that isn't ALSO strongly negative
  would trip a delta threshold — this collapses into "DexScreener must also show a big dip",
  which is a second, undocumented liquidity-style filter, not a plausibility guard. It would have
  correctly caught the real incident, but at the cost of rejecting the ordinary case in spec.md's
  User Story 2 (DexPaprika -31%, DexScreener -22%, same direction — a ~9-point gap that a delta
  threshold tuned to catch a 60-point sign-flip would almost certainly also reject if set tight,
  or fail to catch the real incident if set loose enough to tolerate that gap).
- *A ratio-based check* (`price_change_24h / var_24h_pct` implausible), mirroring
  `EXIT_PRICE_SANITY_MULTIPLE`'s multiplicative design. Rejected: a ratio is undefined/unstable
  near `var_24h_pct == 0` and awkward when the two values have opposite sign (a negative-over-
  negative ratio can look "plausible" even when the magnitudes are wildly different) — the
  exit-side guard's multiplicative design fits a strictly-positive price ratio, not a signed
  percentage-change pair. A direct sign+magnitude rule is simpler and matches the actual failure
  shape observed.
- *No magnitude floor on the DexScreener side (reject on ANY positive reading)*. Rejected: a
  DexScreener reading of, say, +0.5% while DexPaprika reads -30.2% is not a meaningful
  disagreement — it is well within the natural sampling-instant drift the two providers are bound
  to have. `ENTRY_SANITY_MIN_CONFLICT_PCT = 10.0` gives real headroom above that kind of noise
  while still catching the incident's ~29-point positive reading with a wide margin.

**Value chosen**: `10.0` — deliberately conservative (Doctrine d'Ingestion: no empirical
distribution of "how much do these two providers normally disagree" exists yet for this pocket,
since it only started trading today). RECALIBRATE once enough real closed/rejected candidates
accumulate to measure the two providers' typical disagreement empirically (same recalibration
posture already used for `EXIT_PRICE_SANITY_MULTIPLE`/`PEAK_PRICE_SANITY_MULTIPLE` elsewhere in
this dome).

## Decision 2: A missing/zero DexScreener reading is safe by construction, no new sentinel needed

**Decision**: No change to `dexscreener.PairSnapshot`/`_parse_pair`. Confirmed by reading
`services/dexscreener.py`'s `_parse_pair` (`price_change_24h=float(change.get("h24") or 0)`):
there is no "unknown" sentinel for this field today, unlike `liquidity_usd` (which has
`liquidity_unknown: bool` for exactly this ambiguity). A bare `0.0` is therefore ambiguous
between "DexScreener genuinely reported 0% change" and "the field was absent from the response".

**Rationale**: Under Decision 1's rule, this ambiguity is harmless: `0.0` is never
`>= ENTRY_SANITY_MIN_CONFLICT_PCT` (10.0), so a missing/zero reading can never trigger a
rejection — it always falls through to "no disagreement detected", i.e. today's pre-existing
behavior (FR-003). Adding a new sentinel field to `PairSnapshot` to distinguish the two cases
would be additive complexity with no behavioral payoff for this guard, since both cases already
resolve to the same (correct, safe) outcome.

**Alternatives considered**:
- *Add `price_change_24h_unknown: bool` to `PairSnapshot`, mirroring `liquidity_unknown`.*
  Rejected for this feature: real engineering cost (touches a widely-shared dataclass, 60+ call
  sites per its own docstring) for zero behavioral difference under Decision 1's rule. Worth
  reconsidering only if a FUTURE consumer of `price_change_24h` needs to distinguish "0% reported"
  from "unknown" for a reason this guard does not have.

## Decision 3: Rejection is logged via the module's existing `logger.info` convention, not a new registry

**Decision**: On rejection, emit `logger.info("dip_recovery_v2_shadow: entry sanity guard "
"rejected %s (dexpaprika=%.2f%%, dexscreener=%.2f%%)", contract, var_24h_pct,
snapshot.price_change_24h)` — same style already used elsewhere in this module for discovery/
candidate failures (`discover_and_record`'s exception logging, `_resolve_market_cap_and_price`'s
failure logging).

**Rationale**: This module has no `pretrade_rejection_log` wiring today (unlike
`base_momentum_shadow.py`'s `_refuse()` helper, built for the VC/swing main pipeline's own
richer rejection taxonomy). specs/012 deliberately kept this pocket minimal. A distinguishable
log line satisfies FR-005 ("every rejection reason is distinguishable, not silently absorbed")
without importing a heavier mechanism designed for a different pipeline. If this guard's
rejection rate becomes worth tracking quantitatively later (recalibration per Decision 1), a
dedicated counter/table can be added then — not speculatively now.

**Alternatives considered**:
- *Wire `pretrade_rejection_log` (the main momentum_entry.py pipeline's registry).* Rejected:
  that module's schema targets the VC/swing pipeline's own rejection taxonomy (dozens of reasons
  across a much larger surface) — reusing it here for one shadow pocket's one new guard is a
  heavier dependency than the problem needs, and specs/012 already established this pocket's own
  minimal-logging convention deliberately.

## Decision 4: No change to `EXIT_PRICE_SANITY_MULTIPLE` or any exit-side code

**Decision**: This feature is entry-side only. The exit-side guard (specs/012 Decision 2) is
untouched, separately named, separately tested.

**Rationale**: FR-006/FR-007 require the two guards to remain independent. The exit guard solves
a different problem (an implausible take-profit price on an OPEN position) with a different
mechanism (a price-ratio multiplier) — merging them would couple two independently-recalibratable
thresholds for no benefit.
