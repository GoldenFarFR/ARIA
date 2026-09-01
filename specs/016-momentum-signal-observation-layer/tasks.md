---

description: "Task list for the Momentum Signal Observation Layer"
---

# Tasks: Momentum Signal Observation Layer

**Input**: Design documents from `/specs/016-momentum-signal-observation-layer/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/momentum_signal_observation.md, quickstart.md (all present, mutually consistent)

**Tests**: Included — this repo's Permanent Norms require every shipped capability to ship with a test wired into CI.

**Organization**: Tasks are grouped by user story (spec.md P1/P2/P3) so each can be delivered and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 (capture every candidate), US2 (forward performance), US3 (signal availability/staleness)

---

## Phase 1: Setup

- [ ] T001 Read `packages/aria-core/src/aria_core/heartbeat.py` to find the existing cycle-registration pattern (how an entry like `market_sentiment_cycle` declares its interval and gets scheduled). Record the exact pattern to follow in T011 — do not invent a new registration style (research.md §5's open item).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The module and tables every user story writes to/reads from.

- [ ] T002 Create `packages/aria-core/src/aria_core/momentum_signal_observation.py` with the module-level `_ensure_tables()` guard and the two-table DDL from data-model.md (`momentum_signal_observation`, `momentum_signal_forward_performance`), mirroring `dex_score_log.py`'s exact file shape (`aiosqlite`, `aria_core.paths.aria_db_path()`) verified this session by reading `dex_score_log.py` in full.
- [ ] T003 [P] In `packages/aria-core/src/aria_core/momentum_signal_observation.py`, add a read-only helper `_read_signal_cascade_convergence(contract, chain) -> list[dict]` doing a single `SELECT * FROM signal_cascade_convergence WHERE contract=? AND chain=?` (0-4 rows) — this is the first such by-contract reader for that table (research.md §4 confirms none exists yet); do not modify `signal_cascade_convergence.py` itself (FR-009).

**Checkpoint**: Tables exist, module importable, social read helper ready — user story work can begin.

---

## Phase 3: User Story 1 - Capture every evaluated candidate, bought or rejected (Priority: P1) 🎯 MVP

**Goal**: One `momentum_signal_observation` row per momentum-pipeline evaluation (bought or rejected), with on-chain and chart signal families captured, five `pending` forward-performance rows created eagerly, and zero change to the real decision.

**Independent Test**: Evaluate a mix of passing/failing candidates through `momentum_entry.evaluate_momentum_entry`; confirm one row per call regardless of outcome, and confirm `test_momentum_entry.py` is unchanged and green.

### Tests for User Story 1

- [ ] T004 [P] [US1] Test in `packages/aria-core/tests/test_momentum_signal_observation.py`: `capture_observation()` produces exactly one `momentum_signal_observation` row plus five `pending` `momentum_signal_forward_performance` rows, for both a BUY-shaped and a REJECT-shaped `core_result` input (quickstart.md Scenario 1).
- [ ] T005 [P] [US1] Test in `packages/aria-core/tests/test_momentum_signal_observation.py`: on-chain/chart sub-signals absent from `core_result` are recorded as `{"available": false, "reason": "not_evaluated_this_gate"}`, never a zero/neutral value, distinguishing this from a sub-signal that genuinely computed to zero/neutral (quickstart.md Scenario 2, data-model.md's Validation rules).

### Implementation for User Story 1

- [ ] T006 [US1] Implement `capture_observation(contract, chain, core_result, *, extra_context=None)` in `momentum_signal_observation.py` per contracts/momentum_signal_observation.md: extract `decision_action`/`decision_reason`/`reference_price_usd` verbatim from `core_result`'s real shape (dict / bare hold-reason string / `None`, per research.md §1); build `onchain_json` (`composite_score`, `composite_pillars`, `smart_money_rescue_triggered`, `holder_concentration_top10_pct`) and `chart_json` (`golden_pocket_present`, `rsi_divergence_present`, `risk_reward_ratio`, `technical_align_score`, `rvol_confirmed`, `market_regime`) from whatever keys are present, marking absent ones per data-model.md's fixed reason vocabulary; insert the observation row and five `pending` forward-performance rows; wrap the whole function body in try/except that logs and never raises (best-effort, same posture as `narrative_signal_shadow.record_evaluation`).
- [ ] T007 [US1] In `packages/aria-core/src/aria_core/momentum_entry.py`, rename the current `evaluate_momentum_entry` function body to `_evaluate_momentum_entry_core` with zero internal changes, and add a new thin `evaluate_momentum_entry(...)` wrapper (contracts/momentum_signal_observation.md's "Wrapper contract") that: calls the core, calls `momentum_signal_observation.capture_observation(contract, chain, result)` inside a `try/except Exception: pass`, then returns the core's result completely unchanged.
- [ ] T008 [US1] Run the existing `packages/aria-core/tests/test_momentum_entry.py` suite unchanged after T007's rename; confirm every assertion still passes byte-for-byte — this IS the FR-008/FR-009/SC-004 regression gate (quickstart.md Scenario 4), not a new test to write.

**Checkpoint**: Every momentum evaluation now produces a queryable observation row with on-chain/chart signals captured and zero behavioral change — already independently useful (spec.md's own note that US1 alone delivers standalone value).

---

## Phase 4: User Story 2 - Measure forward price performance per observation (Priority: P2)

**Goal**: All five horizons per observation resolve to `measured` (with price/delta) or `unavailable` (with reason) once due, never left `pending` indefinitely, via a dedicated short-cadence cycle — never the existing lazy `signal_cascade_convergence.refresh_forward_prices()` pattern (research.md §3).

**Independent Test**: Seed observations (including ones with no `reference_price_usd`) with due horizons in the past; run the resolver once; confirm every due row resolved to `measured` or `unavailable`, none silently skipped, and confirm at most one `fetch_token_pairs` call per distinct `(contract, chain)` in the batch.

### Tests for User Story 2

- [ ] T009 [P] [US2] Test in `packages/aria-core/tests/test_momentum_signal_observation.py`: `resolve_due_forward_prices()` (a) only selects rows where `status='pending' AND due_at<=now`, (b) deduplicates network calls by `(contract, chain)` when multiple due rows share a token, (c) resolves an observation with `reference_price_usd IS NULL` straight to `unavailable, reason='no_reference_price'` without attempting a network call, (d) marks a token whose price lookup fails as `unavailable` with a reason, and (e) never touches an already-resolved row (quickstart.md Scenario 3).

### Implementation for User Story 2

- [ ] T010 [US2] Implement `resolve_due_forward_prices() -> int` in `momentum_signal_observation.py` per contracts/momentum_signal_observation.md: cheap SQL funnel first (`WHERE status='pending' AND due_at<=now`), dedupe by `(contract, chain)`, call `aria_core.services.dexscreener.fetch_token_pairs(contract, chain)` once per unique token (reusing the existing throttled client, no new throttle), then `UPDATE` each due row to `measured` (with `price_usd`/`pct_change_vs_reference`/`resolved_at`) or `unavailable` (with `unavailable_reason`/`resolved_at`); return the count of rows resolved.
- [ ] T011 [US2] Register a new heartbeat cycle in `packages/aria-core/src/aria_core/heartbeat.py` calling `momentum_signal_observation.resolve_due_forward_prices()` on a ~60s cadence, following the exact registration pattern found in T001 (not a new style).

**Checkpoint**: Forward performance is measurable end-to-end for both bought and rejected candidates.

---

## Phase 5: User Story 3 - Make signal availability and staleness explicit, especially for social (Priority: P3)

**Goal**: Social sub-signals distinguish "not available" from "available and neutral", with each available sub-signal carrying its own `data_timestamp` distinct from the observation's decision timestamp — the correctness property that makes US1's captured data trustworthy for social signals specifically (research.md §4).

**Independent Test**: Evaluate a token never touched by any social-signal cycle — confirm `social_json`'s `signal_cascade_convergence` is `not_available/not_yet_scanned` and `radar_x_signal` is `not_available/no_persisted_state`; evaluate a token with a 40-minute-old `signal_cascade_convergence` row — confirm it reads `available:true` with `data_timestamp` equal to that row's own `recorded_at`, not the decision timestamp.

### Tests for User Story 3

- [ ] T012 [P] [US3] Test in `packages/aria-core/tests/test_momentum_signal_observation.py`: `social_json`'s `conviction_research_score` is `available:true` with `data_timestamp` == decision timestamp when present in `core_result`; `signal_cascade_convergence` reflects 0-4 rows from T003's helper each with the source row's own `recorded_at` (not the decision timestamp) when present, and `not_available/not_yet_scanned` when the helper returns zero rows; `radar_x_signal` is unconditionally `not_available/no_persisted_state` in every observation (quickstart.md Scenario 2, User Story 3's acceptance scenarios).

### Implementation for User Story 3

- [ ] T013 [US3] Extend `capture_observation()` in `momentum_signal_observation.py` to build `social_json`: `conviction_research_score` from `core_result` (when present, `data_timestamp` = decision timestamp), `signal_cascade_convergence` from T003's `_read_signal_cascade_convergence(contract, chain)` helper (each entry's own `recorded_at` as its `data_timestamp`; `not_available/not_yet_scanned` when empty), and `radar_x_signal` hardcoded `not_available/no_persisted_state` (research.md §4 — do not add persistence to `radar_x.py`, out of scope per FR-009).

**Checkpoint**: All three signal families are captured with honest availability semantics — the full spec.md architecture (on-chain + chart + social, separately, with forward performance) is now live.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T014 [P] Run quickstart.md's five scenarios end-to-end against a test `DATA_DIR` (via `aria_core.bootstrap.configure_data_dir`) to confirm the implemented feature matches the validation guide written in Phase 1 planning.
- [ ] T015 Once T004-T013's tests are green: deploy per this repo's Zero-Permission Policy (`./vanguard/deploy.sh`), then verify the commit actually served (health check response compared against `git rev-parse main`, never the deploy script's own text output), then update `.claude/last-deployed-ref`.
- [ ] T016 Add a 3-line entry to `docs/HANDOFF_PIPELINE_MOMENTUM.md` per CLAUDE.md's imposed format (`[STATUS] Subject / Date / Problem / Solution — file(short-hash)`), in the same commit as the deploy-verified change, and confirm the file is already indexed in `docs/HANDOFF_INDEX.md` (it is — already listed).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup (T001 informs T011 later, but T002/T003 can start immediately) — BLOCKS all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational. No dependency on US2/US3.
- **User Story 2 (Phase 4)**: Depends on Foundational + the `momentum_signal_observation`/`momentum_signal_forward_performance` tables existing (T002). Does not require US1's wrapper (T007) to be functionally correct, but in practice needs real observation rows to resolve against — sequence after US1 for a meaningful independent test, though the resolver code itself has no import-time dependency on US1's wrapper.
- **User Story 3 (Phase 5)**: Extends US1's `capture_observation()` (T006) directly — sequence after US1's Phase 3 completes.
- **Polish (Phase 6)**: Depends on all three user stories being complete and green.

### Within Each User Story

- Tests before implementation (T004/T005 before T006/T007; T009 before T010; T012 before T013).
- T008 (regression check) only after T007 (the rename it verifies).

### Parallel Opportunities

- T004 and T005 (both new-file tests, no shared state) in parallel.
- T003 (foundational social-read helper) in parallel with T002 once both are scoped, since they touch the same new file but non-overlapping functions — coordinate to avoid a merge conflict in the same file rather than treating as fully independent.
- T009 in parallel with any US1 test once Foundational is done.
- T012 in parallel with T009 (different concerns within the same test file — coordinate on file, not blocked on each other's logic).

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1).
2. **STOP and VALIDATE**: confirm one observation per evaluated candidate, zero decision regression (T008).
3. This alone already delivers a queryable historical record — spec.md's own note that US1 stands on its own.

### Incremental Delivery

1. Setup + Foundational → tables and module exist.
2. US1 → capture live, decision path provably unchanged → deploy (T015-T016 can run after US1 alone if the operator wants an earlier checkpoint, though this plan's default is to complete all three stories first).
3. US2 → forward performance measurable.
4. US3 → social availability semantics correct — the experimental question in spec.md's Success Criteria (SC-006) is only fully answerable once all three stories are live.
