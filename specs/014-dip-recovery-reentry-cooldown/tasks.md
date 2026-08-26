# Tasks: dip_recovery_v2_reentry_cooldown

**Input**: plan.md, research.md, data-model.md, quickstart.md (all in `specs/014-dip-recovery-reentry-cooldown/`)

**Tests**: Included per operator convention for this dome.

## Phase 1: Setup

- [X] T001 Confirm no other pytest process is already running before any test run (`ps aux | grep pytest`).

## Phase 2: Foundational

No new dependencies, no new gate, no schema change. Nothing blocks the user stories below.

## Phase 3: User Story 1 - A token cannot be immediately rebought right after its own position closes (Priority: P1) 🎯 MVP

**Goal**: The exact EARTHCOIN incident (take-profit close, same-price rebuy 15 minutes later) never recurs.

**Independent Test**: Seed a `take_profit_25pct` close 15 minutes ago for a contract, feed a qualifying candidate for the same contract, confirm `opened == 0`.

- [X] T002 [US1] Add `REENTRY_COOLDOWN_MINUTES = 60` constant near `ENTRY_SANITY_MIN_CONFLICT_PCT` in `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`.
- [X] T003 [US1] Add `_recently_closed_via_take_profit(db, contract, chain) -> bool` in `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`: query `SELECT close_reason, closed_at FROM dip_recovery_v2_shadow WHERE contract=? AND chain=? AND status='closed' ORDER BY closed_at DESC LIMIT 1`; return `True` only if a row exists, `close_reason == "take_profit_25pct"`, and elapsed minutes since `closed_at` `< REENTRY_COOLDOWN_MINUTES`.
- [X] T004 [US1] In `_maybe_open_position` (same file), call `_recently_closed_via_take_profit` immediately after the existing `_has_open_position` check (still before `_resolve_market_cap_and_price`) — on `True`, log `logger.info("dip_recovery_v2_shadow: reentry cooldown rejected %s (closed_at=%s, take_profit_25pct, %.1f min ago)", contract, closed_at.isoformat(), minutes_elapsed)` and `return 0`.
- [X] T005 [US1] Add a test-only helper `_seed_closed_position(contract, chain, close_reason, minutes_ago)` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py` that INSERTs a closed row directly (status='closed', the given close_reason, closed_at computed from `minutes_ago`, entry_price/opened_at/pool_address filled with placeholder-but-valid values matching the schema's NOT NULL columns).
- [X] T006 [US1] Add `test_discover_rejects_reentry_within_cooldown_after_take_profit` in the same test file: seed a `take_profit_25pct` close 15 minutes ago for `CONTRACT`, then run `discover_and_record` with a qualifying candidate for the same contract — assert `opened == 0`.

**Checkpoint**: The real incident cannot recur — this alone is a deployable MVP.

## Phase 4: User Story 2 - The cooldown does not treat every close reason identically without an explicit decision (Priority: P2)

**Goal**: Timeout closes are explicitly excluded from the cooldown (research.md Decision 2).

**Independent Test**: Seed a `timeout_max_hold` close 5 minutes ago, feed a qualifying candidate for the same contract, confirm a position still opens.

- [X] T007 [P] [US2] Add `test_discover_opens_after_cooldown_window_elapses` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: seed a `take_profit_25pct` close 2 hours ago — assert `opened == 1`.
- [X] T008 [P] [US2] Add `test_discover_ignores_cooldown_for_timeout_closes` in the same file: seed a `timeout_max_hold` close 5 minutes ago — assert `opened == 1` (timeout never triggers the cooldown).

**Checkpoint**: This guard cannot become an over-broad blanket rule.

## Phase 5: User Story 3 - Every candidate blocked by this cooldown is visible, not silently absorbed (Priority: P3)

**Goal**: A cooldown rejection is distinguishable from every other rejection reason.

- [X] T009 [US3] Add `test_reentry_cooldown_rejection_is_logged_distinctly` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: reuse T006's scenario with `caplog.at_level(logging.INFO, logger=shadow.logger.name)`, assert `"reentry cooldown"` appears in `caplog.text`.

**Checkpoint**: All 3 user stories independently verified — full feature complete.

## Phase 6: Polish

- [X] T010 Regenerate `docs/pocket-parameters.json` via `python -m aria_core.pocket_parameters --write` and review the diff (specs/013 already showed the registry auto-detects new module-level constants — do not assume no action is needed).
- [X] T011 Run targeted suite: `cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py tests/test_coherence.py -q -n auto`.
- [X] T012 Run full suite: `.venv/bin/python -m pytest -q -n auto` (after confirming no concurrent pytest process per T001).
- [X] T013 Add a `[DEPLOYE]` entry to `docs/HANDOFF_PIPELINE_MOMENTUM.md` documenting the EARTHCOIN incident (position id=15/id=16, pool `0x49a11a3515755a730b20ae1d6c3ef5a997e20f728ad46d8859654c4d4eaad95a`, chain=robinhood, 15-minute same-price rebuy) and the fix, referencing `specs/014-dip-recovery-reentry-cooldown` and the commit hash.
- [X] T014 Commit (co-authors Claude + GoldenFarFR), push `origin main`.
- [X] T015 Deploy via `./vanguard/deploy.sh`, verify the commit actually served (`curl` health check vs `git rev-parse main`), update `.claude/last-deployed-ref`.

## Dependencies & Execution Order

- Phase 1 (T001) → Phase 3 (T002-T006, the core guard + primary regression test) → Phases 4/5 (T007-T009, parallel once T002-T005 land) → Phase 6 (T010-T015, sequential).
- T005 (the test-seeding helper) blocks T006, T007, T008, T009 — all four new tests use it.
- T002-T004 (the guard implementation) block every test task.

## Parallel Example

```text
# After T002-T006 land (the guard exists and its primary regression test passes):
Task: "T007 [P] [US2] cooldown expires after the window elapses"
Task: "T008 [P] [US2] timeout closes never trigger the cooldown"
# T009 [US3] depends on T006's scenario existing but is a distinct test — can run alongside T007/T008.
```

## Implementation Strategy

**MVP = Phase 3 (US1) alone**: T002-T006 already stop the exact EARTHCOIN incident from recurring.
Phases 4-5 harden the guard against over-blocking (US2) and untraceable rejections (US3) — real
risks per this dome's own standing doctrines, not optional polish, but the MVP is safe to ship
after Phase 3 alone if time-boxed.
