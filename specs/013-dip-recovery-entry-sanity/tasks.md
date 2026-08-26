# Tasks: dip_recovery_v2_entry_sanity_guard

**Input**: plan.md, research.md, data-model.md, quickstart.md (all in `specs/013-dip-recovery-entry-sanity/`)

**Tests**: Included per operator convention for this dome (every capability ships with a wired test, CLAUDE.md "Testabilité" norm).

## Phase 1: Setup

- [X] T001 Confirm no other pytest process is already running before any test run (`ps aux | grep pytest`), per `feedback_pytest_xdist_free_cores_before_big_runs` convention.

## Phase 2: Foundational

No new dependencies, no new gate, no schema change (data-model.md: pure decision-time filter). Nothing blocks the user stories below.

## Phase 3: User Story 1 - A dip candidate whose two providers disagree on direction never opens a position (Priority: P1) 🎯 MVP

**Goal**: The exact incident (DexPaprika strongly negative, DexScreener strongly positive for the same candidate) never opens a position again.

**Independent Test**: Feed `_maybe_open_position` the real incident's numbers (DexPaprika -31.9487081644224, DexScreener +29.0) and confirm `opened == 0`, no row inserted.

- [X] T002 [US1] Add `ENTRY_SANITY_MIN_CONFLICT_PCT = 10.0` constant near the other pocket constants in `packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`.
- [X] T003 [US1] In `_maybe_open_position` (`packages/aria-core/src/aria_core/dip_recovery_v2_shadow.py`), after `snapshot` is resolved and validated (after the `if snapshot is None or not snapshot.price_usd: return 0` check), add the guard: if `var_24h_pct <= DIP_THRESHOLD_PCT and snapshot.price_change_24h >= ENTRY_SANITY_MIN_CONFLICT_PCT`, log `logger.info("dip_recovery_v2_shadow: entry sanity guard rejected %s (dexpaprika=%.2f%%, dexscreener=%.2f%%)", contract, var_24h_pct, snapshot.price_change_24h)` and `return 0` — before the market-cap/liquidity checks or after, either is fine (research.md: ordering among these checks is not load-bearing), but strictly before the INSERT.
- [X] T004 [US1] Extend the `_snapshot()` helper in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py` with a new `price_change_24h: float = 0.0` parameter (matches `PairSnapshot`'s own default — every existing call site stays unaffected).
- [X] T005 [US1] Add `test_discover_rejects_entry_on_provider_sign_disagreement` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: DexPaprika reading -31.9487081644224 (the exact real incident value), DexScreener `price_change_24h=29.0` via `_snapshot(...)` — assert `opened == 0` and `await _rows()` is empty.

**Checkpoint**: The real incident cannot recur — this alone is a deployable MVP.

## Phase 4: User Story 2 - A missing or unavailable cross-check reading never blocks or fabricates a signal (Priority: P2)

**Goal**: Ordinary provider drift (same direction, or a missing/zero DexScreener reading) never blocks an otherwise-qualifying entry.

**Independent Test**: Feed a candidate with `price_change_24h=0.0` (provider-default/missing) or `price_change_24h=-22.0` (same direction as DexPaprika's -31.0) and confirm a position opens exactly as it would have before this feature.

- [X] T006 [P] [US2] Add `test_discover_opens_on_ordinary_same_direction_disagreement` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: DexPaprika -31.0, DexScreener `price_change_24h=-22.0` via `_snapshot(...)` — assert `opened == 1`.
- [X] T007 [P] [US2] Add `test_discover_opens_when_dexscreener_change_is_missing_or_zero` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: DexPaprika -35.0, DexScreener `price_change_24h=0.0` (the dataclass default) via `_snapshot(...)` — assert `opened == 1`, i.e. identical to pre-feature behavior.

**Checkpoint**: This guard cannot become a silent second liquidity filter.

## Phase 5: User Story 3 - Every refusal from this guard is visible, not silently absorbed (Priority: P3)

**Goal**: A rejection by this guard is distinguishable from every other rejection reason this pocket already produces.

**Independent Test**: Trigger the US1 rejection scenario and assert the log line names this guard specifically.

- [X] T008 [US3] Add `test_entry_sanity_rejection_is_logged_distinctly` in `packages/aria-core/tests/test_dip_recovery_v2_shadow.py`: reuse T005's scenario with `caplog.at_level(logging.INFO)` (same pattern as `tests/test_log_redaction.py`), assert `"entry sanity guard"` appears in `caplog.text`.

**Checkpoint**: All 3 user stories independently verified — full feature complete.

## Phase 6: Polish

- [X] T009 Check `packages/aria-core/src/aria_core/pocket_parameters.py`'s `dip_recovery_v2_shadow` entry — confirmed it is a single description string, not an enumerated-constants list (verified this session), so no edit and no `--write` regeneration needed for this feature.
- [X] T010 Run targeted suite: `cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py tests/test_coherence.py -q -n auto`.
- [X] T011 Run full suite: `.venv/bin/python -m pytest -q -n auto` (after confirming no concurrent pytest process per T001).
- [X] T012 Add a `[DEPLOYE]` entry to `docs/HANDOFF_PIPELINE_MOMENTUM.md` documenting the real incident (position id=13, contract `0x23acfab04106a21af0ae1643b74cfec3c9aac181`, chain=robinhood: DexPaprika -31.9487% vs DexScreener/DexPaprika-live ~+29% minutes later) and the fix, referencing `specs/013-dip-recovery-entry-sanity` and the commit hash.
- [X] T013 Commit (co-authors Claude + GoldenFarFR), push `origin main`.
- [X] T014 Deploy via `./vanguard/deploy.sh`, verify the commit actually served (`curl` health check vs `git rev-parse main`), update `.claude/last-deployed-ref`.

## Dependencies & Execution Order

- Phase 1 (T001) → Phase 3 (T002-T005, the core guard + its primary regression test) → Phases 4/5 (T006-T008, can run in parallel with each other once T002-T004 land, since they only add test cases against the already-implemented guard) → Phase 6 (T009-T014, sequential: docs → tests → commit → deploy).
- T004 (the `_snapshot()` helper extension) blocks T005, T006, T007, T008 — all four new tests use it.
- T002/T003 (the guard implementation) block every test task (T005-T008) — tests assert against real behavior, not written test-first in this case since the exact numbers and rule were already settled in research.md.

## Parallel Example

```text
# After T002-T005 land (the guard exists and its primary regression test passes):
Task: "T006 [P] [US2] ordinary same-direction disagreement still opens"
Task: "T007 [P] [US2] missing/zero DexScreener reading never blocks"
# T008 [US3] depends on T005's scenario existing but is a distinct test — can run alongside T006/T007.
```

## Implementation Strategy

**MVP = Phase 3 (US1) alone**: the guard implementation (T002-T003) plus its primary regression
test (T004-T005) already stops the real incident from recurring. Phases 4-5 harden the guard
against becoming either a silent volume-reducer (US2) or an untraceable rejection (US3) — both
real risks per this dome's own standing doctrines, not optional polish, but the MVP is safe to
ship after Phase 3 alone if time-boxed.
