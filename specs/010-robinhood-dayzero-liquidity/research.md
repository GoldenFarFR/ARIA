# Phase 0 Research: Calibrate Robinhood shadow pocket's day-zero liquidity gate

No `[NEEDS CLARIFICATION]` markers remained in the spec. This phase resolves the one open
design question the spec explicitly flagged as unsettled: threshold vs. timing of the
liquidity measurement — and grounds every other decision in measured data, per the
operator's explicit standard for this chantier ("infrastructure professionnelle, aucune
bidouille, mesures empiriques avant toute décision").

## Decision 1: Timing is already handled correctly — the defect is purely the threshold

**Finding** (verified by reading `OnChainPoolDiscoveryFeed.check_candidates`, `services/
onchain_pool_discovery.py`): a candidate that fails the liquidity floor at one cycle is
**not** dropped — it stays in `self._candidates` and is re-evaluated on every subsequent
call to `check_candidates()` until `_OBSERVATION_WINDOW_SECONDS` (currently **600.0**, i.e.
10 minutes) elapses since its detection. Only then is it expired and dropped (line ~304-306:
`if now - cand.discovered_at >= _OBSERVATION_WINDOW_SECONDS: expired_keys.append(key);
continue`).

**Decision**: the spec's open question ("seuil ou timing, ou les deux") is resolved as
**threshold only** — the existing timing mechanism (retry every cycle, up to 10 minutes of
maturation) already gives a newly created pool a real chance to receive deposits before
being judged. No change to `_OBSERVATION_WINDOW_SECONDS` or the retry mechanism is proposed.

**Rationale**: re-designing a mechanism that is already doing its job correctly would add
complexity without fixing anything — the measured data (Decision 2 below) shows the real
defect is a threshold that the day-zero population essentially never crosses, not a
premature judgment.

**Alternatives considered**: lengthening `_OBSERVATION_WINDOW_SECONDS` beyond 10 minutes —
rejected for now, no evidence a longer window would materially change the liquidity
distribution (Robinhood Chain pools that will receive real deposits typically do so quickly
after creation or not at all, per the operator's own domain knowledge of memecoin launches);
revisit only if post-deployment data (Decision 3) shows otherwise.

## Decision 2: New day-zero-specific liquidity floor, derived from measured rejections

**Measured** (query against `fresh_launch_pretrade_gate_log`, `pocket='robinhood_pump' AND
reason LIKE 'blocked_thin_liquidity%'`, 318 real rows): `would_be_reserve_usd` ranges from
$0 to $3996.5, with **median ≈ 0** (effectively zero), **p75 = $134.0**, **p90 = $2460.4**.

**Important caveat, stated honestly** (per the operator's standard — no shortcuts): this
distribution is **left-censored** — it only contains candidates that were already rejected
by the existing 4000$ floor, each of which had already had up to 10 minutes to mature under
Decision 1's retry mechanism. It is NOT the distribution of all day-zero candidates ever
seen (qualified ones are absent from this table by construction). This is the best data
available before this fix ships; Decision 3 defines how it gets corrected post-deployment.

**Decision**: introduce a new constant, `MIN_LIQUIDITY_USD_DAY_ZERO`, set to **$200** as a
conservative provisional floor (Doctrine d'Ingestion: a documented conservative hypothesis
beats leaving the gap unguarded or guessing a round number). Rationale for $200 specifically:
it sits above the near-zero median (filters genuine dust/never-funded pools) and well below
p75 ($134 is already inside the "rejected" population, so a real qualifying pool's floor
should be lower than what the OLD 4000$ floor rejected at its lower end) — while remaining
high enough to exclude the pools this feature must never treat as tradeable (per FR-008, the
pre-23/08 defect: pools whose entry reserve averaged $6.40).

**MIN_LIQUIDITY_USD (4000.0, DexPaprika fallback path) is explicitly left untouched** — it
was validated on 200 real trades (61.8% winrate, FR-001) and this feature must not regress it.

**Alternatives considered**:
- Setting the day-zero floor to $0 (accept everything): rejected — would reopen the
  pre-23/08 defect (FR-008) the instant a genuinely dead pool with $0.01 reserve is treated
  as tradeable.
- Setting it at the measured p90 ($2460): rejected as too permissive given the censoring
  caveat above — p90 of a *rejected* population is not evidence that pools above it are
  good, only that they were rare among rejections; a lower, more conservative number is the
  correct read of "we don't know the qualifying population's true shape yet."
- Reusing the exact 4000$ constant with a "day zero" comment explaining why it's actually
  fine: rejected outright — the measured data directly contradicts this (median rejection
  is at $0, meaning virtually nothing clears 4000$).

## Decision 3: Both duplicated filter sites must read the SAME entry-mode-aware value

**Finding**: the floor is applied twice today — once inside `OnChainPoolDiscoveryFeed.
check_candidates(min_liquidity_usd=...)` (discovery-level, called from `shadow_persistent.
py:robinhood_discovery_loop` with the hardcoded `robinhood_pump_shadow.MIN_LIQUIDITY_USD`),
and again inside `record_signals()` in both `robinhood_pump_shadow.py` (line ~809) and
`robinhood_pump_v2_shadow.py` (line ~205, importing the same constant).

**Decision**: `record_signals()` in both v1 and v2 must select the floor based on the
`entry_mode` parameter it already receives (`"day_zero"` → `MIN_LIQUIDITY_USD_DAY_ZERO`,
anything else → the existing `MIN_LIQUIDITY_USD`), and the call site in `shadow_persistent.
py` must pass the same entry-mode-aware value to `check_candidates()`. This keeps exactly
one source of truth per entry mode, read consistently at both filter sites, in both pocket
variants.

**Rationale**: `entry_mode` already exists as the discriminator threaded through the whole
call chain (`shadow_persistent.py` → `record_signals(..., entry_mode=entry_mode)`) —
reusing it avoids introducing a second, parallel way to distinguish the two paths.

**Alternatives considered**: removing the `record_signals`-level check entirely (relying
only on the discovery-level filter) — rejected, because `record_signals` is also the direct
entry point for the DexPaprika fallback path, which does NOT pass through
`check_candidates()` at all; removing the check there would leave that path unguarded.

## Decision 4: Recalibration protocol for the +25%/trade target (User Story 3)

**Decision**: no numeric threshold is set now. The protocol is: (1) accumulate day-zero
closures after this fix ships; (2) once **n≥100** closures exist (existing project-wide
doctrine), run the equivalent of `pocket_entry_sweep` against `MIN_LIQUIDITY_USD_DAY_ZERO`
and any other `_at_entry` columns already collected; (3) apply the existing statistical
guardrails (outlier removal for top-2/top-5, day-count coverage check) before treating any
resulting number as real; (4) if n stays below 100 after a reasonable period (e.g. 30 days),
document that explicitly as a finding about Robinhood Chain's real volume rather than forcing
a number.

**Rationale**: this mirrors the exact discipline already mandated project-wide (CLAUDE.md's
"Analysing a pocket = run `pocket_entry_sweep` FIRST" and the outlier-removal guardrail) —
no new methodology invented, just applied on schedule once real data exists.

**Alternatives considered**: setting an interim target now based on the DexPaprika-path's
historical 25.42%/trade (with its own 4000$ floor) — rejected, that number describes a
different population (established/trending pools) and has no bearing on what a day-zero
population's realistic per-trade return looks like.
