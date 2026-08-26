---
description: "Task list for recalibrating the Solana late-bonding shadow pocket's regime gate"
---

# Tasks: Recalibrate the Solana late-bonding shadow pocket's regime gate

**Input**: Design documents from `/specs/011-solana-regime-recalibration/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md (all present)

**Tests**: Included in each user story's implementation phase (project-wide doctrine requires
every capability to ship with a wired-in test) rather than as a separate TDD-first phase.

**Organization**: Single-constant change (`REGIME_MIN_MEDIAN_PEAK_PCT`, one file), so
Foundational and US1 are nearly the same edit — Foundational lands the value change itself,
US1 covers the deploy/restart that makes it take effect, US2 locks it with a test, US3
documents the recalibration protocol in-code.

## Phase 1: Setup

- [X] T001 Run the existing test suite for the affected module
      (`cd packages/aria-core && .venv/bin/python -m pytest
      tests/test_solana_late_bonding_shadow.py -q`) to confirm a clean baseline before any
      edit in this feature

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Land the recalibrated constant with its full rationale in-code.

**⚠️ CRITICAL**: Do not start Phase 3+ until this is in place.

- [X] T002 In `packages/aria-core/src/aria_core/solana_late_bonding_shadow.py`, change
      `REGIME_MIN_MEDIAN_PEAK_PCT` from `40.0` to `30.0` (~line 594), replacing the existing
      comment block with one documenting: this recalibration (2026-08-26, operator-directed),
      research.md Decision 1's real recomputed open-time table (20%→37.6%, 25%→19.8%,
      30%→11.5%, 35%→6.4%, 40%→4.0%, n=4602 rolling medians), why 30.0 over 25.0 (safety
      margin from the excluded 20% bar, measured capture-gap being a structural exit-mechanics
      property not re-provable per-threshold today), and the recalibration protocol (research.md
      Decision 2: n≥100 provisional, n≥1000 for this spec's own closure per SC-004) — append
      to, never delete, the existing 20→50→30→25→40 history comment already there

**Checkpoint**: New threshold value and its full rationale exist in the codebase — user story
work can begin.

---

## Phase 3: User Story 1 - The pocket resumes producing closures without reopening the capture-gap defect (Priority: P1) 🎯 MVP

**Goal**: Unblock the regime gate, closed ~94-96% of the time since 2026-08-24's raise to 40%,
without reopening the capture-gap defect that justified that raise.

**Independent Test**: query `solana_late_bonding_shadow_log` after deployment — new rows with
`detected_at`/`exit_reason` after the fix ships (quickstart.md step 3).

### Implementation for User Story 1

- [X] T003 [US1] Restart the out-of-repo process (`systemctl restart
      aria-shadow-persistent.service`) — this fix has no effect until this ships, since this
      process (not the Docker container) actually runs `solana_late_bonding_shadow`'s
      discovery/exit loops and imports `REGIME_MIN_MEDIAN_PEAK_PCT` directly

**Checkpoint**: Gate recalibrated and live. Independently verifiable via quickstart.md step 3
once enough wall-clock time has passed for new candidates to clear the lower bar.

---

## Phase 4: User Story 2 - The recalibration is traceable and derived from real measurement (Priority: P2)

**Goal**: Lock the new value as a tested invariant and make the rationale checkable by a
future session, not just readable in a comment.

**Independent Test**: a dedicated test asserts the constant's value; `docs/pocket-parameters.json`
and the HANDOFF entry cite the old value, the new value, and the measured trade-off table.

### Implementation for User Story 2

- [X] T004 [US2] Add a test in `packages/aria-core/tests/test_solana_late_bonding_shadow.py`
      asserting `pocket.REGIME_MIN_MEDIAN_PEAK_PCT == 30.0` — locks the recalibrated value so a
      future edit can't silently drift it back without updating this test and its rationale
- [X] T005 [US2] Regenerate `docs/pocket-parameters.json`
      (`python -m aria_core.pocket_parameters --write`) and review the diff — confirm only the
      value (40.0 → 30.0) and the `why` field change, no unrelated line-number drift left
      unreviewed

**Checkpoint**: The new value is not just "it unblocks trading" but verifiably locked and
traceable to its own measured rationale.

---

## Phase 5: User Story 3 - A recalibration protocol exists toward the +25%/trade target (Priority: P3)

**Goal**: Document how and when `REGIME_MIN_MEDIAN_PEAK_PCT` (and this pocket's other
parameters) get recalibrated further, gated on a real sample — no number forced beyond 30.0
today.

**Independent Test**: the protocol is written down (in-code, per T002's comment, and in
research.md Decision 2), states the exact n≥100 and n≥1000 gates, and states explicitly what
happens if the sample stays insufficient.

### Implementation for User Story 3

- [X] T006 [US3] Verify T002's comment block fully covers the recalibration protocol (n≥100
      provisional capture-gap re-measurement, n≥1000 for this spec's SC-004 closure,
      statistical guardrails re-applied) — add anything missing rather than relying on
      research.md alone, since the in-code comment is what a future session actually reads
      first

**Checkpoint**: All three user stories complete — implementation "Done". Spec closure is a
separate, later milestone (see Closure section below).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T007 Run the full affected-module suite plus project-wide coherence:
      `cd packages/aria-core && .venv/bin/python -m pytest
      tests/test_solana_late_bonding_shadow.py tests/test_coherence.py -q`
- [X] T008 Archive `solana_late_bonding_shadow_log` (same pattern as
      `solana_late_bonding_shadow_log_archive_reset_20260825`) at the moment this fix deploys
      — this fix changes the entry style (lower regime threshold), so it starts a NEW epoch
      per spec.md SC-004; the 1000-trade closure count for the +25%/trade closure criterion
      must start from zero here, never blended with pre-recalibration closures
- [X] T009 Commit the fix (`solana_late_bonding_shadow.py`, its test, regenerated
      `pocket-parameters.json`) with a HANDOFF entry in `docs/HANDOFF_PIPELINE_MOMENTUM.md`
      documenting the root cause (gate closed ~94-96% of the time) and the fix (threshold +
      rationale + epoch reset); push per the operator's line-threshold policy
- [X] T010 Deploy: `systemctl restart aria-shadow-persistent.service` (already covered by T003
      — re-verify it is still the running process after this commit's push, since a Docker
      redeploy in between would not affect this out-of-repo process, but a second manual
      restart of the SAME process is a no-op worth confirming rather than assuming)
- [ ] T011 Run quickstart.md step 3 again after enough wall-clock time has passed (hours to
      low single-digit days, consistent with ~11.5% simulated open-time) to confirm SC-001
      (closures genuinely resume, not just "should resume")

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup (baseline confirmed green). BLOCKS all user
  stories — nothing downstream matters before the constant itself changes.
- **User Stories (Phase 3-5)**: All depend on Foundational. US1 (T003, the restart) is the only
  way the change takes effect at all; US2 (T004-T005) locks and documents it; US3 (T006)
  verifies the recalibration protocol is fully written down. These are lightly sequential in
  practice (T003 before observing anything, T004-T006 independent of wall-clock time) rather
  than needing strict ordering among themselves.
- **Polish (Phase 6)**: Depends on all three user stories being complete.

### Parallel Opportunities

- T004 (test) and T005 (pocket-parameters regen) touch different files — genuinely
  parallelizable.
- T003 (restart) has no file dependency and could run any time after T002 — but doing it right
  after T002 (rather than waiting for T004-T006) means the wall-clock observation window in
  T011 starts sooner, so it is sequenced early in Phase 3 rather than deferred to Polish.

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1 (T001) + Phase 2 (T002).
2. Complete Phase 3 (US1: T003) — the gate is recalibrated and live. This alone is a complete,
   independently-verifiable fix for the reported symptom (94-96% closed gate, 15h+ of silence).
3. **STOP and VALIDATE**: run quickstart.md step 3 after enough time has passed.

### Full Delivery (recommended, matches specs/010's precedent)

1. Setup + Foundational.
2. US1 (recalibrate + deploy) → US2 (lock the invariant with a test, regenerate the parameter
   registry) → US3 (verify the recalibration protocol is fully documented).
3. Polish (Phase 6): full suite, epoch-archive reset, HANDOFF entry, deploy verification with
   real wall-clock data — never declare success from the code change alone.

---

## Closure

**T001-T011 completing marks the implementation "Done" — it does NOT close this spec.**
Per spec.md SC-004 (mirrors specs/010's own closure format): mark this spec's Status "Closed"
only once the **average realized return across ≥1000 closed trades reaches +25% minimum**, all
within the SAME epoch (the period since T008's reset — this recalibration itself starts a new
epoch). If a later parameter change starts a newer epoch before 1000 same-epoch closures
accumulate, the count restarts there too; this is expected behavior, not a stall. Statistical
guardrails apply throughout (outlier removal top-2/top-5, day-count coverage check — same as
every other pocket calibration in this project). Until the current epoch reaches 1000 closures
averaging ≥+25%, this spec stays open and gets revisited periodically (e.g. alongside the
recurring `pocket_entry_sweep` passes), never silently abandoned once the code ships. A HANDOFF
entry documenting the final validated average (or the documented reason it never arrived, or
which epoch reset it) accompanies the eventual closing commit.
