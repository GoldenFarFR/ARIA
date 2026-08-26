# Phase 1 Data Model: Calibrate Robinhood shadow pocket's day-zero liquidity gate

## Entities

### Day-zero candidate (in-memory, `OnChainPoolDiscoveryFeed._candidates`)
- **Attributes**: `pool_address`/`token_address`, `discovered_at` (monotonic clock), latest
  `reserve_usd`/`price_usd` snapshot from the WS feed.
- **Lifecycle**: created on `PairCreated`/`PoolCreated`; re-evaluated against the liquidity
  floor on every `check_candidates()` call; removed either when qualified (returned to the
  caller) or when `_OBSERVATION_WINDOW_SECONDS` (600s) elapses without qualifying.
- **Change this feature makes**: the floor it's evaluated against becomes entry-mode-aware
  (`MIN_LIQUIDITY_USD_DAY_ZERO` instead of the DexPaprika-calibrated `MIN_LIQUIDITY_USD`).

### `MIN_LIQUIDITY_USD_DAY_ZERO` (new constant, `robinhood_pump_shadow.py`, imported by
`robinhood_pump_v2_shadow.py`)
- **Value**: $200 (provisional, per research.md Decision 2).
- **Validation**: must never be silently applied to the DexPaprika/`m5_surge` entry mode —
  selection is keyed on the existing `entry_mode` parameter.
- **Relationship**: read at both filter sites (`check_candidates()` call in `shadow_persistent
  .py`, and the `record_signals()` internal check) — single source of truth per entry mode.

### `fresh_launch_pretrade_gate_log` (existing table, unchanged schema)
- **Role**: already records every rejection (`reason`, `would_be_reserve_usd`, `decided_at`)
  — this feature relies on it, unchanged, to (a) diagnose the current defect and (b) supply
  the post-deployment data the Decision 4 recalibration protocol needs. No schema change.

### `robinhood_pump_regime_candidates_log` / `robinhood_pump_v2_shadow`'s equivalent
- **Role**: unchanged schema — this feature's success is measured by this table receiving
  new rows again (SC-001), not by any structural change to it.

## State transitions

```
Pool created (PairCreated/PoolCreated event)
  -> candidate tracked (discovered_at = now)
  -> [retried every discovery cycle, up to 600s]
       reserve_usd >= floor(entry_mode)?
         yes -> qualified, passed to record_signals(entry_mode="day_zero")
                 -> record_signals' OWN floor check (same floor(entry_mode)) confirms
                 -> position recorded / candidate logged
         no  -> stays tracked, re-checked next cycle
  -> window expires (600s) without qualifying -> dropped, uncounted today (Edge Case:
     this feature does not change this -- a candidate that matures too late is correctly
     excluded, not a defect)
```

No new persistent state is introduced — the fix changes which floor value is read at two
existing decision points, plus adds the new constant and the entry-mode branch that selects
between it and the existing one.
