---

description: "Task list for the dip-recovery v2 shadow pocket"
---

# Tasks: Dip-recovery shadow pocket, v2 -- Base/Robinhood market-cap-bounded dip entry

**Input**: Design documents from `/specs/012-dip-recovery-v2/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md (all present)

**Tests**: included -- a full test file already exists (`test_dip_recovery_v2_shadow.py`, 14
tests, 13 green, 1 red) and is extended rather than rewritten.

**Organization**: most of this pocket's code already exists as an uncommitted draft. Tasks below
are framed as fixes/additions to that draft, not a from-scratch build.

## Phase 1: Setup

- [ ] T001 Confirm the draft files are present and importable: `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`, `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`. No new scaffolding needed -- skip straight to Foundational.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the dedup fix (Decision 1) changes the table schema and the dedup logic every user
story's tests rely on -- it must land first.

- [ ] T002 In `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`, remove the `dip_recovery_v2_shadow_episode_state` `CREATE TABLE` block from `_ensure_tables()` entirely.
- [ ] T003 In the same file, rewrite `_advance_episode` (or fold its logic directly into `discover_and_record`) to dedupe via `SELECT 1 FROM dip_recovery_v2_shadow WHERE contract = ? AND chain = ? AND status = 'open'` instead of reading/writing `in_episode` -- skip opening if a row is found, otherwise proceed to the entry filters and insert as today.
- [ ] T004 In `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`, remove the `_episode_state` helper and every assertion against it (`test_discover_opens_position_on_qualifying_dip`, `test_discover_rejects_market_cap_above_band`, `test_discover_never_reopens_mid_episode`) -- replace with direct row-based assertions (e.g. "exactly one open row exists for this contract/chain").
- [ ] T005 In the same file, confirm `test_discover_rearms_after_recovery_above_threshold` now passes unmodified in its scenario (still: open, "recover", re-dip -> second row) but reflecting that re-arming no longer depends on ever observing the recovery pass -- it now works because the first position's `status` never blocks the third call once dedup is keyed on `status='open'` and nothing changed that. Adjust only the assertions that referenced `_episode_state`, not the scenario itself.

**Checkpoint**: `pytest tests/test_dip_recovery_v2_shadow.py -q` is fully green before proceeding.

---

## Phase 3: User Story 1 - Dual-chain wiring (Priority: P1) 🎯 MVP

**Goal**: the pocket screens both Base and Robinhood in the same heartbeat pass, never one chain
silently missing.

**Independent Test**: `test_run_cycle_covers_both_chains` (already exists) passes; `run_cycle()`'s
`CHAINS` tuple is unchanged (`("base", "robinhood")`).

### Implementation for User Story 1

- [ ] T006 [US1] In `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`, confirm `CHAINS = ("base", "robinhood")` and `run_cycle()`'s loop over `CHAINS` are unaffected by the Phase 2 changes (no code change expected -- this is a verification task, not a build task).
- [ ] T007 [US1] In `packages/aria-core/src/aria_core/heartbeat.py`, confirm the already-drafted `HeartbeatTask` (`id="dip_recovery_v2_shadow_cycle"`), gate-check block (`ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED`), and dispatch (`dip_recovery_v2_shadow.run_cycle()`) call the module's `run_cycle()` directly (which itself already iterates both chains) -- no per-chain wiring needed at the heartbeat level.

**Checkpoint**: `test_run_cycle_covers_both_chains` green; heartbeat wiring calls the pocket's own dual-chain `run_cycle()`.

---

## Phase 4: User Story 2 - Bounded entry population (Priority: P1)

**Goal**: entry requires the market-cap band, liquidity floor, AND (new) minimum pool age --
never a day-zero or micro-cap token.

**Independent Test**: a candidate with a pool younger than 14 days is rejected before any
DexScreener call is made.

### Tests for User Story 2

- [ ] T008 [P] [US2] In `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`, add `test_discover_rejects_pair_younger_than_minimum_age`: a `TrendingPool` with `pool_created_at` set to 5 days ago must be rejected before `dexscreener.fetch_token_pairs` is called (assert the mock was never invoked, same pattern as the existing `test_discover_ignores_dip_under_threshold`).
- [ ] T009 [P] [US2] In the same file, add `test_discover_rejects_pair_with_unknown_age`: a `TrendingPool` with `pool_created_at=None` must be rejected (never fabricated as "old enough" by default) before any paid call.
- [ ] T010 [P] [US2] In the same file, add `test_discover_accepts_pair_at_or_above_minimum_age`: a `TrendingPool` with `pool_created_at` set to exactly 14 days ago (or older) proceeds to the DexScreener call and, given passing market-cap/liquidity, opens a position with `entry_pool_age_days` populated and >= 14.0.

### Implementation for User Story 2

- [ ] T011 [US2] In `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`, add `MIN_POOL_AGE_DAYS = 14.0` alongside the other threshold constants, and add an `entry_pool_age_days REAL` column to the `dip_recovery_v2_shadow` `CREATE TABLE` in `_ensure_tables()`.
- [ ] T012 [US2] In `discover_and_record`'s candidate loop, compute pool age from `pool.pool_created_at` (already populated by `dexpaprika.get_trending_pools`, no extra network call) and `continue` past any candidate where `pool_created_at is None` or age-in-days `< MIN_POOL_AGE_DAYS` -- placed alongside the existing `var_24h` check, before the DexScreener call (funnel doctrine, per research.md Decision 3).
- [ ] T013 [US2] Pass the computed age through to the position-open path (`_advance_episode`/its Phase-2 replacement) so `entry_pool_age_days` is stored on the inserted row.

**Checkpoint**: T008-T010 pass; every opened row has `entry_pool_age_days >= 14.0`.

---

## Phase 5: User Story 3 - Simple exit, protected against a corrupted quote (Priority: P1)

**Goal**: fixed +25% take-profit, 7-day timeout, no stop-loss -- and (new) a guard against a
corrupted/implausible exit price falsely triggering a take-profit close.

**Independent Test**: an exit quote implying an implausible jump (e.g. 10000x entry) never closes
the position on a phantom take-profit.

### Tests for User Story 3

- [ ] T014 [P] [US3] In `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`, add `test_exit_check_ignores_implausible_price_jump`: an open position's exit snapshot returns a price 10,000x the entry price -- the position must remain `status='open'` after `advance_open_positions`, never closed as `take_profit_25pct`.
- [ ] T015 [P] [US3] In the same file, add `test_exit_check_processes_price_just_under_sanity_bar_normally`: an exit price just under the sanity multiplier but still >= the +25% target closes normally as `take_profit_25pct` -- confirms the guard doesn't over-reject genuine take-profits.

### Implementation for User Story 3

- [ ] T016 [US3] In `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`, add `EXIT_PRICE_SANITY_MULTIPLE` (choose the value in the plan/implementation step by the same reasoning as `PEAK_PRICE_SANITY_MULTIPLE=1000.0` on `base_momentum_shadow.py` -- document the choice in a comment, per research.md Decision 2).
- [ ] T017 [US3] In `_advance_one_position`, before computing `pnl_pct`/`close_reason`, skip the check for this pass (treat as unavailable, retried next pass, never fabricate a close) if `snapshot.price_usd > entry_price * EXIT_PRICE_SANITY_MULTIPLE` -- same "log and skip" pattern as `base_momentum_shadow.py`'s own guard.
- [ ] T018 [US3] Confirm `test_take_profit_closes_position_at_25pct` and `test_timeout_closes_stale_position_without_a_stop_loss` (already existing) still pass unmodified after T017 (their price deltas are well under any reasonable sanity multiplier).

**Checkpoint**: all of Phase 5's tests green; no stop-loss code path exists anywhere in the module (grep confirms).

---

## Phase 6: Polish & Deployment

**Purpose**: registry/coherence upkeep, full-suite verification, and shipping.

- [ ] T019 Regenerate `docs/pocket-parameters.json` (`cd packages/aria-core && .venv/bin/python -m aria_core.pocket_parameters --write`) and review the diff -- confirm the new `dip_recovery_v2_shadow` entry reflects `MIN_POOL_AGE_DAYS`, `EXIT_PRICE_SANITY_MULTIPLE`, and every other constant from this pocket.
- [ ] T020 Confirm no other pytest process is already running (`ps aux | grep pytest`), then run the targeted suite: `cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py tests/test_coherence.py -q`.
- [ ] T021 Run the full suite: `cd packages/aria-core && .venv/bin/python -m pytest -q -n auto` -- zero regressions.
- [ ] T022 Add a `docs/HANDOFF_PIPELINE_MOMENTUM.md` entry, format `[CODE]` (tested, not yet deployed -- the gate still needs the operator's own `.env` edit), citing this spec directory and the two real fixes found this session (dedup redesign, exit price-sanity guard).
- [ ] T023 Commit (`Co-Authored-By: Claude <noreply@anthropic.com>` + `Co-Authored-By: GoldenFarFR <sylvain.rio.fr@gmail.com>`, per project convention), push to `main`.
- [ ] T024 Deploy (`./vanguard/deploy.sh`), then verify the commit ACTUALLY being served (`curl` the health check, compare to `git rev-parse main`) -- never trust the script's own text output.
- [ ] T025 Update `.claude/last-deployed-ref` to the verified commit hash and commit that update.
- [ ] T026 Run `quickstart.md`'s checks #1 (full test suite) now; checks #2-#6 depend on the gate being enabled and real heartbeat passes accumulating, so they are follow-up verification, not part of this deployment.
- [ ] T027 Explicitly tell the operator, in the session's own reply (not automated by this task list): the gate `ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED` still needs to be set to `true` in `vanguard/backend/.env` by the operator themself -- direct `.env` writes are structurally blocked this session.

---

## Dependencies & Execution Order

- **Setup (T001)**: no dependencies.
- **Foundational (T002-T005)**: depends on T001. BLOCKS every user story -- the dedup rewrite changes the schema and the core open/skip logic all three stories' tests exercise.
- **User Story 1 (T006-T007)**: depends on Foundational. Pure verification, no code change expected -- can run in parallel with US2/US3's test-writing.
- **User Story 2 (T008-T013)**: depends on Foundational. Independent of US1/US3.
- **User Story 3 (T014-T018)**: depends on Foundational. Independent of US1/US2.
- **Polish (T019-T027)**: depends on all of US1+US2+US3 being complete and green.

### Parallel Opportunities

- T008, T009, T010 (US2 tests) can be written in parallel -- same file, but non-overlapping test functions.
- T014, T015 (US3 tests) can be written in parallel with T008-T010 (different concerns, same file -- serialize the actual edits, but the test logic can be drafted independently).
- US1's verification (T006-T007) can run alongside US2/US3's implementation -- it touches no shared code path.

## Implementation Strategy

### MVP First

Foundational (dedup fix) is the true MVP gate here -- without it, nothing in this pocket behaves
correctly regardless of which user story ships first. Once Foundational is green, US1's
verification is nearly free (T006-T007 confirm existing behavior), so the practical order is:
Foundational -> US2 (age filter) -> US3 (price-sanity guard) -> US1 verification -> Polish.

### Incremental Delivery

Each user story's checkpoint is independently testable via its own pytest subset before moving to
Polish -- but because this is a single-module shadow pocket (not a multi-surface product), all
three stories ship together in one deployment (T024), not staggered releases.
