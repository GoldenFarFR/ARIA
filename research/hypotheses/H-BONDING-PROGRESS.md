# H-BONDING-PROGRESS — later entry on the Pump.fun bonding curve improves risk/reward

status: **REFUTED IN-SAMPLE (2026-09-02, same day it was pre-registered)** —
falsifier #4 was run and the hypothesis did not survive it. The pre-registered
definition below is left EXACTLY as it was written before the test; only this
header and the "Falsifier #4 result" section were added afterwards. That
ordering is the whole point — the falsifier was fixed before the measurement,
so the result cannot be an after-the-fact rationalisation.

A refutation is a success of the protocol, not a failure of it: this is the
first hypothesis ARIA has actually tried to kill, and it died in a few hours
against its own recorded data.

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
   with `reserve_usd` at entry). **PARTIALLY DEMONSTRATED ALREADY, 02/09 —
   this falsifier is no longer merely pending.** The pocket's own code says
   so: "the SAME liquidity floor that was supposed to protect this band was
   ALSO lowered 5500$ -> 4000$ the same day -- so the one thing the test's
   justification leaned on never actually held". `MIN_BONDING_PROGRESS` and
   `MIN_LIQUIDITY_USD` were moved on the same days and re-checked on the same
   samples (578 then 1609 closures), so their respective gains do not add up
   and neither is attributable alone. Any revival of this hypothesis must
   decompose the two effects BEFORE claiming anything — a partial-correlation
   check is now a precondition, not a nice-to-have.

## Falsifier #4 result — RUN 2026-09-02, HYPOTHESIS DOES NOT SURVIVE

**Method**: stratify the frozen cohort (`archive_floor3000_20260822`, n=1655,
one stop config) into liquidity terciles by `reserve_usd` at entry, then
re-measure the bonding-progress effect INSIDE each stratum. An effect that
vanishes at comparable liquidity was liquidity, not bonding progress.

**Finding 1 — the two groups barely overlap, so the original comparison was
never like-for-like.**

| bonding progress | liq. tercile 1 | tercile 2 | tercile 3 | total |
|---|---|---|---|---|
| low (<70%) | **477** | 36 | 1 | 514 |
| high (>=70%) | 70 | 511 | 546 | 1127 |

93% of low-progress entries sit in the lowest-liquidity tercile; 94% of
high-progress entries sit in terciles 2-3. Bonding progress and entry
liquidity are very nearly the same variable in this dataset. The headline
result (31.5% -> 48.0% winrate) compares two populations that differ in
liquidity at least as much as in bonding progress.

**Finding 2 — where a real comparison IS possible (tercile 1, n=472 vs 64),
the effect disappears and slightly reverses once outliers are removed.**

| tercile 1 | n | all | minus top 2 | minus top 5 | winrate |
|---|---|---|---|---|---|
| low progress | 472 | 0.924 | 0.866 | **0.838** | 29.9% |
| high progress | 64 | 0.933 | 0.839 | **0.774** | 40.6% |

Both lose money. After removing each group's 5 best closures, the high-progress
group is *worse*, not better — its apparent edge was more outlier-dependent
than the low-progress group's.

**Finding 3 — tercile 2's apparent reversal is itself an outlier artifact, and
I nearly reported it as a result.** Raw numbers there read low 1.332 vs high
1.120, i.e. an inversion. Removing the top 5: low collapses to **0.896** (n=40,
single best trade 13.13x) while high holds at **1.084** (n=495). The inversion
was carried by two trades. This is recorded because I stated the inversion
out loud before running the outlier test — the exact ordering error the
standing mandate forbids ("un chiffre présenté sans son n et sans son test
d'outliers est une opinion, pas une mesure"). The test was run seconds later
and reversed the reading.

**Finding 4 — the whole cohort spans 3 distinct days.** 1655 closures, 3 days.
Same failure mode as the 22/08 incident (439 closures all from a single day).
A large n here is not coverage.

**Verdict**: the effect is not attributable to bonding progress. It is
inseparable from entry liquidity on this data, and in the one stratum where
they can be separated it does not survive an outlier test. Falsifier #4 is
DEMONSTRATED, not merely suspected.

**What this does NOT establish**: that late bonding entries are worthless. It
establishes that THIS evidence never supported the claim. A properly designed
test — matched liquidity by construction, several distinct days, out-of-sample
cohort — has never been run. The hypothesis is not disproven about the market;
it is disproven about our data.

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
