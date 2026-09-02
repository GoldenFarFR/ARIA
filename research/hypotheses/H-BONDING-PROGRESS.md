# H-BONDING-PROGRESS — later entry on the Pump.fun bonding curve improves risk/reward

status: PRE_REGISTERED (2026-09-02) — falsifiers and cohort frozen below,
verdict not yet run through a Phase-2 resolver (Phase 2 core does not exist
yet, cf. `piped-percolating-dream` plan). This file freezes the definition so
the exploratory numbers already produced cannot silently mutate once the lab
exists.

## Why this hypothesis, and not another, first

Selected from the 10-pocket registry as the first Phase-1 candidate because
it is the richest-documented mechanism with the most partial falsification
attempts already recorded (fixed-stop rejected, ladder rejected,
`MAX_TOP_BUYER_SHARE` explicitly non-robust to cross-validation in its own
code comment) — exactly the profile Phase 1 should promote first: a claim
that has already tried to kill itself several times and hasn't fully
succeeded.

## Economic hypothesis

Entering a Pump.fun bonding-curve pool later in its progress (closer to
migration to PumpSwap) produces a better risk/reward than entering early.

## Source of the inefficiency

Early-curve entries carry two costs a later entry avoids: (1) a much higher
probability the curve never migrates at all (abandoned/rugged before
graduation — survivorship never reaches this dataset, which only records
detections that produced a trackable entry, a bias to flag, not yet
corrected); (2) higher price impact per dollar on a thinner curve. Late
entries buy confirmation (the token already proved it can attract enough
volume to approach migration) at the cost of already-realized upside.

## Mechanism

`bonding_progress` (0-1, `aria_core.solana_late_bonding_shadow`, derived from
`bonding_progress()` in the curve-state module) measured at entry. Higher
progress = closer to the fixed SOL-reserve migration threshold.

## ARIA's capability

Real-time curve-state polling already wired (`resolve_bonding_curves`) —  no
new data source required to act on this if validated.

## Horizon

Minutes to a few hours (bonding curves on Pump.fun typically fill in that
range, not days).

## Domain

Solana, Pump.fun bonding curves only, pre-migration. Does not generalize to
Base/Robinhood Chain (no bonding-curve mechanism there) or to already-migrated
Solana pools (`solana_pump_shadow`'s domain, a different pocket).

## Falsifiers (pre-registered, before any Phase-2 run)

1. Effect sign flips or vanishes on a cohort the parameter was never tuned
   against (out-of-sample, different time window, different token set).
2. Effect is entirely carried by the top 2 or top 5 closures (outlier
   artifact) — tested below, survives on this cohort, must be re-tested on
   every future cohort.
3. Effect disappears once execution/impact modelling replaces
   `realistic_final_multiplier`'s current approximation with the Phase-2
   resolver's `execution.v1` contract (this dataset's "realistic" price is
   not yet the resolver's `P_EXECUTABLE`).
4. Effect is actually a liquidity proxy in disguise (progress correlates
   with `reserve_usd` at entry) — not yet decomposed; a partial-correlation
   check against liquidity is required before this hypothesis is called
   independent of the liquidity-convergence hypothesis (Family B, see
   registry).

## Counterfactual

The pocket's own witness/control: a random entry-time policy on the same
pool population, same instant, same resolver — not yet run (no witness
mechanism exists before Phase 2).

## Costs

Not yet decomposed (gross return only, no separate price-impact/slippage/gas
terms) — this dataset predates the Phase-2 resolver's `execution.v1`
contract.

## Multiplicity

`hypotheses_previously_tested_in_late_bonding_pocket`: at least 9 distinct
parameters were explored on overlapping data in this pocket's history
(bonding progress, migration, liquidity, buyer count, top-buyer
concentration, re-entry cooldown, regime, trailing-stop band, fixed stop) —
this is one of those 9, not an isolated test.
`hypotheses_tested_in_family`: grouped under Family A (Opportunity timing)
alongside pool age and migration status in `solana_late_bonding_shadow` and
`solana_fresh_launch*` — these are correlated measurements of the same
underlying "how far along is this opportunity," not independent
confirmations.

## Verified evidence (this session, 2026-09-02, re-derived directly from
`shadow.db`, never taken from a prior report on trust)

**Cohort fragmentation confirmed and worse than described**: the pocket's
production table was reset/reconfigured 12 times since 22/08
(`solana_late_bonding_shadow_log_archive_*`), each under a different stop
config, none sharing a `config_hash`. Total closed positions across all 12:
2,721. The current live table alone (post-26/08 reset) has only 37 closed
positions — any headline figure spanning "all closures" mixes incompatible
stop-loss regimes. This is exactly the `config_hash` gap the sealed plan
already flagged, confirmed empirically rather than assumed.

**Single-cohort replication, `archive_floor3000_20260822` (n=1655 closed,
one stop config, the largest homogeneous cohort available)**:

| bonding progress at entry | n | winrate | avg multiplier |
|---|---|---|---|
| 40-60% | 372 | 31.5% | 0.930 |
| 60-70% | 145 | 31.0% | 1.023 |
| 70-80% | 654 | 48.0% | 1.113 |
| 80-100% | 484 | 46.1% | 1.137 |

**Outlier test (top 5 removed per bucket, per CLAUDE.md's mandatory check)**:
70-80% avg multiplier 1.113 → **1.077** (n=649); 80-100% avg multiplier
1.137 → **1.068** (n=479). Direction and winrate both hold after removing the
5 best closures in each bucket — this is not a pure outlier artifact on
*this* cohort.

**What this does NOT establish**: only one homogeneous cohort tested,
in-sample (same data the parameter thresholds were originally read from,
falsifier #1 not yet run), no partial-correlation against liquidity
(falsifier #4), no resolver-grade execution modelling (falsifier #3), no
witness/control comparison (counterfactual not yet run). Status stays
**EXPLORATORY** — the same downgrade the operator applied to the 97.6%/1.68x
figure elsewhere in the registry, for the same reason.

## Multiplicity/family cross-references

See `piped-percolating-dream` plan (sealed) for the full Family A/B/C/D/E
taxonomy this hypothesis belongs to. Family B (liquidity as execution
quality, appearing independently in Robinhood/Fresh Launch/Late
Bonding/Solana Pump) is the next candidate to check against falsifier #4
above before this hypothesis can be called liquidity-independent.
