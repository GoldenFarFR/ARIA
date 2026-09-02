# Inter-pocket dependency graph — the pockets are not independently deletable

    Measured 2026-09-02 by AST/grep across packages/aria-core/src/aria_core/,
    while preparing Phase 4 of the sealed plan ("Noyau indépendant + poches
    jetables"). Nothing here is inferred from documentation.

## Why this matters

Invariant **I5** of the plan states a pocket is a disposable plugin: ARIA must
be able to LOSE a pocket without losing any data, proof or capability. Measured
today, that is **false**: deleting one pocket breaks up to four others, because
six shared utilities live INSIDE pockets rather than in the core.

This is also why a deletion pass cannot proceed pocket-by-pocket in arbitrary
order. It is not a code-tidiness observation — it is an ordering constraint on
Phase 4.

## The graph (10 edges, measured)

```
robinhood_pump_v2                  --> robinhood_pump
solana_fresh_launch                --> solana_pump
solana_fresh_launch_fast_discovery --> solana_fresh_launch
                                   --> solana_fresh_launch_ws_exit
                                   --> solana_pump
solana_fresh_launch_ws_exit        --> solana_fresh_launch
                                   --> solana_fresh_launch_fast_discovery
                                   --> solana_pump
solana_late_bonding                --> solana_fresh_launch_ws_exit
                                   --> solana_pump
```

**`solana_pump` is imported by 4 other pockets.** It has become the project's
de-facto shared utility module, wearing the name of a trading pocket. Deleting
it first breaks four pockets at once.

**A mutual cycle exists**: `fast_discovery` <-> `ws_exit`. It does not crash at
import time only because both sides do their imports LOCALLY, inside function
bodies (`solana_fresh_launch_ws_exit_shadow.py` lines 761 and 1214) — the
classic workaround for a circular import. The dependency is real even though
Python tolerates it.

## What actually crosses those edges — a hidden core, never named as one

The same six primitives travel over nearly every edge:

| Primitive | Kind | Lives in | Consumed by |
|---|---|---|---|
| `_apply_price_impact_and_fee` | execution model | solana_pump, robinhood_pump, base_momentum (3 copies) | all 6 edges |
| `_minutes_since` | time helper | solana_pump / robinhood_pump | all 6 edges |
| `_snapshot_with_fallback` | observation | solana_pump / robinhood_pump | all 6 edges |
| `SIMULATED_TRADE_SIZE_USD` | execution parameter | solana_pump / robinhood_pump | all 6 edges |
| `PoolSnapshot` | **type** | solana_pump / ws_exit | late_bonding |
| `_epoch_of` | time helper | solana_pump | fresh_launch, fast_discovery |

By the plan's own sorting rule — *what observes/measures belongs to the core,
what decides stays in the pocket* — every one of these is core material:
execution model, time, observation, a shared type. None of them is a decision.
They are exactly the "branchements intelligents" worth recovering before
anything is deleted.

**`_apply_price_impact_and_fee` is still duplicated in 3 pockets** despite
`market_impact.py` having been extracted earlier the same day. That extraction
freed `executability_replay.py` (its purpose) but never de-duplicated the
pockets themselves, which still carry their own copies. Whether the three
copies are byte-identical to `market_impact.apply_price_impact_and_fee` is NOT
established here and must be verified before any of them is assumed
redundant — a silently diverged copy is exactly how a "safe" cleanup changes
behaviour.

## A false independence, directly relevant to Phase 1

`robinhood_pump_v2` imports its THRESHOLDS from `robinhood_pump`:
`MIN_LIQUIDITY_USD`, `MIN_LIQUIDITY_USD_DAY_ZERO`, `M5_SURGE_THRESHOLD_PCT`,
`MAX_POOL_AGE_MINUTES`, `LIQUIDITY_COLLAPSE_EXIT_PCT`, plus `regime_state` and
`record_regime_candidate`.

So v2 is not a second, independent pocket testing a separate hypothesis — it is
a **parameterised variant of v1 sharing v1's entry thresholds**. Any future
registry entry must count them as ONE hypothesis in the same family, never as
two independent confirmations. This is precisely the multiplicity illusion the
plan's `MULTIPLICITÉ` field exists to prevent (`hypotheses_tested_in_family`,
not `hypotheses_previously_tested`).

## Consequence for Phase 4 ordering

A deletion pass must either:
1. lift the six shared primitives into the core FIRST (they are core material
   by the plan's own criterion), then delete pockets in any order; or
2. delete strictly in reverse-dependency order, accepting that the primitives
   die with the last pocket standing — which would silently remove capability
   the core is supposed to keep, violating I5 in the other direction.

Option 1 is the one the plan describes. Option 2 satisfies "the code is
disposable" while quietly breaking "the experiment is not".

`evaluate_exit()` (`solana_fresh_launch_ws_exit_shadow.py:1291-1583`) is a
seventh piece in the same situation and is treated separately: the plan names
it as the resolver's seed for Phase 2. Verified pure by AST (293 lines, zero
I/O calls), and its signature already separates observations
(`current_price`, `reserve_usd`, `window_high`, `window_low`, `age_minutes`)
from decision thresholds (`hard_stop_pct`, `trailing_stop_pct`,
`trailing_arm_peak_pct`, `max_hold_minutes`, `profit_ladder`,
`fixed_stop_pct`) — the exact core/pocket boundary the plan draws. Extraction
must keep the mechanism and leave the thresholds as caller-supplied
parameters; promoting the defaults into the core would move decision into the
core, which the plan forbids.
