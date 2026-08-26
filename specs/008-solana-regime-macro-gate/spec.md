# Feature Specification: Macro regime gate, wider pre-arm, full metric persistence for late-bonding

**Feature Branch**: `008-solana-regime-macro-gate`

**Created**: 2026-08-26

**Status**: In progress (closes when no further bug/improvement is found)

**Input**: Operator-directed, 3 parts, following a live diagnostic of `solana_late_bonding_shadow`'s regime gate (see `docs/HANDOFF_PIPELINE_MOMENTUM.md` 26/08 entry): (1) replace the 40%-median endogenous regime gate with a macro sensor built from curve-tracker-wide graduation velocity; (2) widen trade-stream pre-arm from 50% to 30% bonding progress, raising `MAX_WATCHED_MINTS`; (3) persist `bonding_progress`/`sell_pressure_slope`/`buys_observed`/`sells_observed`/peak-timing at full coverage for future backtests.

## Scope

`solana_late_bonding_shadow.py` (regime gate, screen_candidate metrics), `pumpfun_trade_stream.py` (pre-arm/watch cap), `pretrade_rejection_log.py` + `solana_regime_candidates_log` schema (persistence). Shadow/paper only, zero real capital.

## User Stories

### Part 3 — Full metric persistence (Priority: P1, done first — zero strategy risk)

Instrument what is already computed in `screen_candidate`'s `metrics` dict but silently dropped before reaching `GateDecision`/`solana_regime_candidates_log`: `bonding_progress`, `sell_pressure_slope` (already in `metrics`, just never passed), `buys_observed`/`sells_observed` (from `flow.buy_count`/`flow.sell_count`, confirmed to exist on `TokenTradeFlow`). Add a `bonding_progress` column to both tables (hot ALTER, Doctrine d'Ingestion). Pure data-collection change, no trading-behavior change — Fast-Track by the CLAUDE.md router (no guardrail, no strategy parameter, covered by new tests).

**Acceptance**: a fresh `consider_candidate` call populates all 4 columns non-NULL on both `fresh_launch_pretrade_gate_log` and `solana_regime_candidates_log`, on both accepted and blocked paths.

### Part 1 — Macro regime sensor (Priority: P1, build in OBSERVATION mode first)

Build a sensor from the curve tracker's own tracked population (already free, in-memory, independent of this pocket's filters): graduations/hour (mints reaching `complete=True` or leaving the tracked set past `MAX_BONDING_PROGRESS`), logged alongside the existing endogenous gate's verdict for direct comparison.

**Operator instruction was to REPLACE the 40%-median gate outright. Adjusted here, and why**: the current gate's own history (see `REGIME_MIN_MEDIAN_PEAK_PCT`'s in-code comment, 23/08) already records ONE prior gate rebuild that measured -0.18%/trade in production against a wrongly-optimistic simulation, because it was validated on paper before going live. Swapping the only active filter for an unvalidated one carries the same risk. Built in observation-only mode (logged, never gating) for a measurement window before any swap — Doctrine d'Ingestion says instrument now, not "trade blind on an unverified sensor."

**Acceptance**: `solana_regime_candidates_log` (or a sibling table) carries the macro sensor's reading alongside every existing row for >=100 candidates before any gate-swap decision is proposed back to the operator.

### Part 2 — Wider pre-arm window (Priority: P2, staged, not a single jump to 35)

Extend `PRE_ARM_PROGRESS` toward 30% and raise `MAX_WATCHED_MINTS` accordingly.

**Operator instruction was cap=35 immediately. Adjusted here, and why**: the +14-mint estimate is a SINGLE snapshot (`curve_tracker_state.json`, one instant), not a measurement across time — the same "never guess a throughput, always measure" doctrine that governs every other Chainstack/Helius calibration in this project (`docs/HANDOFF_RESOURCE_BUDGET.md`). Helius bills by streamed BYTES, not by watch-slot count, so the real cost of +14 concurrent mints needs a live measurement, not a single photo. Staged: raise the cap to a smaller intermediate value first (e.g. 20), measure GB/day for a few hours, then decide the final number (35 or otherwise) against real data.

**Acceptance**: real GB/day measured at the intermediate cap before moving to the final value; never a single-snapshot extrapolation treated as a measured fact.

## Success Criteria

- **SC-001**: Parts 1/3 deployed and observed for >=100 candidates before Part 1's gate-swap is proposed to the operator.
- **SC-002**: Part 2's final cap is set from a real multi-hour GB/day measurement, never a single snapshot.

## Assumptions

- Shadow/paper only — no real-capital pocket reads any of these signals.
- The existing 40%-median gate stays ACTIVE (not disabled) throughout Part 1's observation window — no gap in filtering while the macro sensor accumulates data.
