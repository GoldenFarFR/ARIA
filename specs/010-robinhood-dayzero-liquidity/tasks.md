---
description: "Task list for calibrating Robinhood shadow pocket's day-zero liquidity gate"
---

# Tasks: Calibrate Robinhood shadow pocket's day-zero liquidity gate

**Input**: Design documents from `/specs/010-robinhood-dayzero-liquidity/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md (all present)

**Tests**: Included in each user story's implementation phase (project-wide doctrine requires
every capability to ship with a wired-in test) rather than as a separate TDD-first phase.

**Organization**: Tasks are grouped by user story. US1 (unblock sourcing) and US2 (judgment
coherence + no regression) share the same underlying code change — US1 delivers it, US2 adds
the tests that lock the invariant and guard the pre-23/08 defect. This is not duplication:
US1 is independently observable (candidates resume flowing) without US2's tests existing yet,
but shipping without US2 would leave the invariant unverified.

## Phase 1: Setup

- [X] T001 Run the existing test suites for the affected modules
      (`cd packages/aria-core && .venv/bin/python -m pytest tests/test_robinhood_pump_shadow.py
      tests/test_robinhood_pump_v2_shadow.py tests/test_onchain_pool_discovery.py -q`) to
      confirm a clean baseline before any edit in this feature

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Introduce the new entry-mode-scoped constant both pocket variants will read.

**⚠️ CRITICAL**: Do not start Phase 3+ until this is in place.

- [X] T002 Add `MIN_LIQUIDITY_USD_DAY_ZERO = 200.0` in
      `packages/aria-core/src/aria_core/robinhood_pump_shadow.py`, next to the existing
      `MIN_LIQUIDITY_USD = 4000.0`, with a comment citing research.md Decision 2 (provisional,
      derived from the 318-row `fresh_launch_pretrade_gate_log` measurement, left-censored
      caveat, recalibrate once n≥100 day-zero closures exist)
- [X] T003 [P] Import `MIN_LIQUIDITY_USD_DAY_ZERO` alongside the existing `MIN_LIQUIDITY_USD`
      import in `packages/aria-core/src/aria_core/robinhood_pump_v2_shadow.py`

**Checkpoint**: New constant exists and is importable — user story work can begin.

---

## Phase 3: User Story 1 - Robinhood shadow pocket resumes seeing real candidates (Priority: P1) 🎯 MVP

**Goal**: Unblock the day-zero sourcing flow, silent since 2026-08-25 23:00 UTC, without
reopening the pre-23/08 near-zero-liquidity defect.

**Independent Test**: query `robinhood_pump_regime_candidates_log` after deployment — new
rows with `decided_at` after the fix ships (quickstart.md step 3).

### Implementation for User Story 1

- [X] T004 [US1] In `packages/aria-core/src/aria_core/robinhood_pump_shadow.py`'s
      `record_signals()`, replace the fixed `MIN_LIQUIDITY_USD` liquidity-floor check
      (~line 809) with an entry-mode-aware lookup: `entry_mode == "day_zero"` →
      `MIN_LIQUIDITY_USD_DAY_ZERO`, anything else → `MIN_LIQUIDITY_USD` (unchanged)
- [X] T005 [US1] Apply the identical entry-mode-aware lookup in
      `packages/aria-core/src/aria_core/robinhood_pump_v2_shadow.py`'s `record_signals()`
      (~line 205)
- [X] T006 [US1] In `/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py`'s
      `robinhood_discovery_loop()`, update the `check_candidates(min_liquidity_usd=...)` call
      to pass `MIN_LIQUIDITY_USD_DAY_ZERO` when `_ROBINHOOD_DISCOVERY_FEED is not None`
      (day-zero path active), keeping `robinhood_pump_shadow.MIN_LIQUIDITY_USD` for the
      DexPaprika fallback branch
- [X] T007 [US1] Restart the out-of-repo process (`systemctl restart
      aria-shadow-persistent.service`) — this fix has no effect until this ships, since this
      process (not the Docker container) actually runs `robinhood_discovery_loop`

**Checkpoint**: Sourcing unblocked. Independently verifiable via quickstart.md step 3 once
enough wall-clock time has passed for new pools to appear on-chain.

---

## Phase 4: User Story 2 - Liquidity judgment stays coherent and safe (Priority: P2)

**Goal**: Lock the entry-mode branch as a tested invariant (same rule at both filter sites,
in both pocket variants) and prove the pre-23/08 defect (near-zero-liquidity positions)
cannot reappear under the new, lower day-zero floor.

**Independent Test**: for both filter sites and both pocket variants, `entry_mode="day_zero"`
uses the new floor and every other mode uses the untouched 4000$ floor; a pool whose reserve
stays near-zero is still rejected.

### Implementation for User Story 2

- [X] T008 [P] [US2] Add a test in
      `packages/aria-core/tests/test_robinhood_pump_shadow.py`: `record_signals(...,
      entry_mode="day_zero")` accepts a pool with reserve above `MIN_LIQUIDITY_USD_DAY_ZERO`
      but below `MIN_LIQUIDITY_USD`, while `entry_mode="m5_surge"` (or the DexPaprika default)
      still rejects that same pool
- [X] T009 [P] [US2] Add the equivalent test in
      `packages/aria-core/tests/test_robinhood_pump_v2_shadow.py`
- [X] T010 [US2] Add a regression test (either file) proving a pool whose reserve stays below
      `MIN_LIQUIDITY_USD_DAY_ZERO` (e.g. $10, matching the pre-23/08 defect's measured $6.40
      average) is rejected under `entry_mode="day_zero"` too — the lower floor must not
      reopen the fixed defect
- [X] T011 [US2] Run quickstart.md step 2 (grep both filter sites and the
      `shadow_persistent.py` call site) — confirm no site was left reading the old fixed
      constant unconditionally

**Checkpoint**: The fix is not just "it unblocks trading" but verifiably safe and coherent
across every site that applies it.

---

## Phase 5: User Story 3 - Recalibration protocol exists for the +25%/trade target (Priority: P3)

**Goal**: Document how and when `MIN_LIQUIDITY_USD_DAY_ZERO` (and the pocket's other
parameters) get recalibrated toward the operator's stated target, gated on a real sample —
no number forced today.

**Independent Test**: the protocol is written down, states the exact n≥100 gate and the
existing statistical safeguards to apply, and states explicitly what happens if the sample
stays insufficient.

### Implementation for User Story 3

- [X] T012 [US3] Add a comment block next to `MIN_LIQUIDITY_USD_DAY_ZERO` in
      `packages/aria-core/src/aria_core/robinhood_pump_shadow.py` documenting the
      recalibration protocol (per research.md Decision 4 and spec.md SC-005): run
      `pocket_entry_sweep` for a first provisional pass once n≥100 day-zero closures exist
      (outlier removal top-2/top-5, day-count coverage check applied) — but the +25%/trade
      floor itself is only considered VALIDATED, and this spec only closeable, once **n≥1000**
      closed trades confirm it under the same statistical guardrails (operator-directed
      2026-08-26). Explicitly note the do-nothing-yet fallback if the sample stalls well below
      either gate for an extended period.

**Checkpoint**: All three user stories complete — implementation "Done". Spec closure is a
separate, later milestone (see Closure section below).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T013 Run the full affected-module suite plus project-wide coherence:
      `cd packages/aria-core && .venv/bin/python -m pytest tests/test_robinhood_pump_shadow.py
      tests/test_robinhood_pump_v2_shadow.py tests/test_onchain_pool_discovery.py
      tests/test_coherence.py -q`
- [X] T014 Regenerate `docs/pocket-parameters.json` (`python -m aria_core.pocket_parameters
      --write`) if any tracked constant's line number shifted, and review the diff
- [X] T014b Archive `robinhood_pump_shadow_log` and its v2 equivalent (same pattern as
      `robinhood_pump_shadow_log_archive_reset_20260825` / `_archive_nofloor_age25_20260823`)
      at the moment this fix deploys — this fix changes the entry style (new day-zero
      liquidity floor), so it starts a NEW epoch per spec.md SC-005; the 1000-trade closure
      count for the +25%/trade closure criterion must start from zero here, never blended
      with pre-fix closures
- [X] T015 Commit the git-tracked half of the fix (robinhood_pump_shadow.py,
      robinhood_pump_v2_shadow.py, tests, regenerated pocket-parameters.json) with a HANDOFF
      entry in `docs/HANDOFF_PIPELINE_MOMENTUM.md` (or the most relevant existing HANDOFF)
      documenting the root cause and fix; push per the operator's line-threshold policy
- [X] T016 Deploy: run `./vanguard/deploy.sh` for the Docker-tracked half (already covered by
      T007's `systemctl restart` for the out-of-repo half) — verify the served commit via
      health check, not the script's own output text
- [ ] T017 Run quickstart.md step 3 again after enough wall-clock time has passed (1-24h) to
      confirm SC-001 (sourcing genuinely resumed, not just "should resume")

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup (baseline confirmed green). BLOCKS all user
  stories — nothing can reference `MIN_LIQUIDITY_USD_DAY_ZERO` before it exists.
- **User Stories (Phase 3-5)**: All depend on Foundational. US1 and US2 touch the same three
  files (record_signals x2, shadow_persistent.py) — implement US1 first (T004-T007), then
  add US2's tests (T008-T011) against the already-changed code, rather than interleaving.
  US3 (T012) is a pure documentation addition, independent of US1/US2's code but logically
  follows them (references the constant they introduce).
- **Polish (Phase 6)**: Depends on all three user stories being complete.

### Parallel Opportunities

- T002/T003 (Foundational) touch different files — could run in parallel, but T003 is a
  one-line import that trivially depends on T002 existing, so sequential is simpler and just
  as fast in practice.
- T008/T009 (US2 tests) touch different test files — genuinely parallelizable.
- T004/T005 (US1) touch different files (v1 vs v2) — genuinely parallelizable, both depend
  only on Phase 2 being done.

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1 (T001) + Phase 2 (T002-T003).
2. Complete Phase 3 (US1: T004-T007) — sourcing resumes. This alone is a complete,
   independently-verifiable fix for the reported symptom (15h+ of silence).
3. **STOP and VALIDATE**: run quickstart.md step 3 after enough time has passed.

### Full Delivery (recommended, matches the operator's "infrastructure professionnelle,
aucune bidouille" standard)

1. Setup + Foundational.
2. US1 (unblock) → US2 (lock the invariant with tests, guard the pre-23/08 defect) → US3
   (document the recalibration protocol).
3. Polish (Phase 6): full suite, pocket-parameters regeneration, HANDOFF entry, deploy both
   halves (Docker + systemd restart), post-deploy verification with real wall-clock data —
   never declare success from the code change alone.

---

## Closure

**T001-T017 completing marks the implementation "Done" — it does NOT close this spec.**
Per spec.md SC-005 (operator-directed 2026-08-26, refined same day): mark this spec's Status
"Closed" only once the **average realized return across ≥1000 closed trades reaches +25%
minimum**, all within the SAME epoch (the period since T014b's reset — this fix itself starts
a new epoch, since it changes the entry style). If a later parameter change starts a newer
epoch before 1000 same-epoch closures accumulate, the count restarts there too; this is
expected behavior, not a stall. Statistical guardrails apply throughout (outlier removal
top-2/top-5, day-count coverage check — same as every other pocket calibration in this
project). Until the current epoch reaches 1000 closures averaging ≥+25%, this spec stays
open and gets revisited periodically (e.g. alongside the recurring `pocket_entry_sweep`
passes), never silently abandoned once the code ships. A HANDOFF entry documenting the final
validated average (or the documented reason it never arrived, or which epoch reset it) 
accompanies the eventual closing commit.
