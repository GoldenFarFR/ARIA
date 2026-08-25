# Feature Specification: Shadow pockets tuning -- Robinhood Chain

**Feature Branch**: `004-shadow-robinhood`

**Created**: 2026-08-25

**Status**: In progress (closes when no further bug/improvement is found)

**Input**: Same operator mandate as `002-shadow-base`. Explicit operator flag (25/08): "je crois que les cloture de robinhood etait fausse verifie" -- and "commence par robinhood c'est celle qui a le plus de volume et de meilleur resultat (sans compter l'echec du shadow)".

## Scope

`robinhood_pump_shadow.py` -- runs OUTSIDE Docker, standalone process (`shadow_persistent.py`, `/opt/aria-data/shadow.db`), same infra as the Solana pocket.

## User Scenarios & Testing

### User Story 1 - Verify whether the reported closures are real (Priority: P1, urgent)

**Why this priority**: the operator flagged a direct suspicion BEFORE any tuning work starts -- per the project's "never validate a system's own prices with its own data" doctrine (22/08 incident), this must be checked against an EXTERNAL source before the headline number (+27.78% without top5 on ~93-98 closures, `nofloor_age25_20260823` generation) is trusted for anything.

**Independent Test**: pick concrete trade ids from the largest-sample generation, pull their `pool_address`/`token_address`/`detected_at`/`peak_price`/`realized_proceeds`, and cross-check the peak/exit price against an external source (DexScreener chart, on-chain read) for that exact pool and time window.

**Acceptance Scenarios**:
1. **Given** a trade whose `final_multiplier`/`realistic_final_multiplier` looks implausibly high, **When** cross-checked against an external chart, **Then** either the peak genuinely occurred (real edge) or it's an artifact (wash-trade spike, stale/duplicate price feed, a bug in how `peak_price`/`realized_proceeds` is computed) -- report which, with the concrete evidence, never a guess.
2. **Given** `robinhood_pump_shadow_log_archive_nofloor_age25_20260823` has 217 total rows but only 98 have `realistic_final_multiplier` populated, **When** investigated, **Then** determine whether the missing 119 are a benign schema-migration gap (column added after these rows were written) or hide a systematically worse/better subset (selection bias check already done 25/08: raw `final_multiplier` on the full set tracks close to the `realistic` figure on the populated subset per exit_reason bucket -- 0.96/1.33/1.28 raw vs -6.6%/+29.34%/+35.79% realistic on the non-null rows -- no gross distortion found so far, but the WHY of the NULLs is still unexplained).

**Status at spec creation (25/08)**: investigation in progress -- first row checked (id=383, "aaaaaaaa" token) showed `last_price` far below `entry_price` post-closure, initially flagged as suspicious; code review clarified `final_multiplier` is computed from `realized_proceeds` (the actual simulated sell fills along the trailing-stop/scale-out ladder), NOT from `last_price` (which keeps updating after close for monitoring only) -- so that specific observation is NOT necessarily a bug on its own. The real external cross-check (peak price vs an outside source) has not been done yet -- this is the next concrete step.

### User Story 2 - Once verified, confirm the real PnL against the +25%/1000 bar (Priority: P2)

Only proceed here once User Story 1's verdict is in -- a headline PnL figure built on unverified closures is not a number to act on.

## Success Criteria

- **SC-001**: A concrete, evidenced verdict on whether the reported robinhood_pump_shadow closures are real or artifact-inflated, backed by at least one external cross-check, before any PnL figure from this pocket is trusted.
- **SC-002**: If confirmed real, PnL average >= +25% on >= 1000 closed positions (outlier-resistant figure) on the actively-running generation.

## Assumptions

- The 25/08 "confirmed no parallel session" state may not hold in a future session -- re-check `ps aux` before resuming work here.
