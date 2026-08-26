---
description: "Task list for restructuring CLAUDE.md into dedicated docs/ files"
---

# Tasks: Restructure CLAUDE.md into dedicated docs/ files

**Input**: Design documents from `/specs/009-restructure-claude-md/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md (all present)

**Tests**: Not requested as new automated tests — verification runs through the two existing
coherence gates (`test_claude_md_stays_under_size_budget`, `test_constitution_is_in_sync_with_claude_md`)
plus the manual grep/diff checks already specified in `quickstart.md`.

**Organization**: All three user stories edit the same file (`CLAUDE.md`) sequentially — they
are independently *verifiable* (each has its own grep/diff check) but not independently
*committable* in parallel, since a partial CLAUDE.md edit mid-sequence would still need the
constitution regenerated to stay in sync. One final commit bundles all three stories plus the
regenerated constitution (see Implementation Strategy).

## Phase 1: Setup

**Purpose**: Create the new destination file before anything moves into it.

- [ ] T001 Create `docs/HANDOFF_INDEX.md` with a short header (purpose: per-component HANDOFF
      description index, one entry per `docs/HANDOFF_<component>.md`; note that any new
      HANDOFF file must add its entry here in the same commit, mirroring the rule CLAUDE.md
      already states for itself)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the baseline before any content edit, so a later regression can't be
confused with pre-existing failure.

**⚠️ CRITICAL**: Do not start Phase 3+ until this passes.

- [ ] T002 Run `cd packages/aria-core && python3 -m pytest tests/test_coherence.py -q` and
      confirm a clean baseline pass before any CLAUDE.md edit in this feature
- [ ] T003 [P] Extract the current HANDOFF block verbatim (`awk '/^## Index of HANDOFF files by component/,/^## Format de réponse/' CLAUDE.md`) to a scratch file for the zero-loss diff check in US1 (T007) — scratch file, not committed

**Checkpoint**: Baseline confirmed green, reference snapshot saved — user story work can begin.

---

## Phase 3: User Story 1 - HANDOFF index extraction (Priority: P1) 🎯 MVP

**Goal**: Move the ~30+ per-component HANDOFF descriptions out of CLAUDE.md into
`docs/HANDOFF_INDEX.md`, keeping every component name grep-able directly in CLAUDE.md via a
one-line pointer.

**Independent Test**: `grep` CLAUDE.md for a component name (e.g. `HANDOFF_CHAINSTACK`) — still
found; the matched line points to `docs/HANDOFF_INDEX.md`; that file carries the original
description verbatim.

### Implementation for User Story 1

- [ ] T004 [US1] Copy each component's original description verbatim from the extracted block
      (T003) into `docs/HANDOFF_INDEX.md`, one entry per `docs/HANDOFF_<component>.md`, same
      wording as today — no rewriting, no summarizing further
- [ ] T005 [US1] Replace the detailed block in `CLAUDE.md` (the "Index of HANDOFF files by
      component" section) with a compact name list plus a one-line pointer to
      `docs/HANDOFF_INDEX.md`; keep the existing "index it in the same commit" rule, now
      pointing at the new file instead of CLAUDE.md directly
- [ ] T006 [US1] Run `quickstart.md` step 2 (every HANDOFF name still grep-able in CLAUDE.md) —
      fix any name that silently dropped out before proceeding
- [ ] T007 [US1] Run `quickstart.md` step 3 (verbatim diff of the extracted block vs.
      `docs/HANDOFF_INDEX.md`) — zero information loss confirmed, not assumed

**Checkpoint**: CLAUDE.md's largest relocatable block is gone; size should already drop by
~4KB. Independently verifiable and, if needed, independently deployable as the MVP slice.

---

## Phase 4: User Story 2 - Resolved historical residue removed (Priority: P2)

**Goal**: Remove the operational-sounding "Active state — pocket lineup" residue (v8/v9/megacap
retirement narrative) beyond what's needed to point to its HANDOFF entry — while leaving the
four backlog-index mentions of "v8" (#279, #371, #374, #377) untouched, per research.md
Decision 2 (they name a still-referenced pattern, not the retired pocket).

**Independent Test**: `grep -n -iE '\bv8\b|\bv9\b|megacap' CLAUDE.md` — the narrative paragraph
is gone or reduced to a pointer; the four backlog lines are still present, unchanged.

### Implementation for User Story 2

- [ ] T008 [US2] Trim the "Active state — pocket lineup" paragraph in `CLAUDE.md` to a short
      pointer to `docs/HANDOFF_PIPELINE_MOMENTUM.md` (2026.08.18 entry) for the v8/v9/megacap
      retirement detail — keep only what still carries operational meaning today (e.g. which
      pockets are currently active: swing + vc, solana_late_bonding_shadow, support-bounce v1/v2)
- [ ] T009 [US2] Run `quickstart.md` step 4 — confirm the narrative residue is gone AND the
      four backlog-index "v8" references (#279, #371, #374, #377) are untouched

**Checkpoint**: Residue removed without collateral damage to unrelated backlog entries.

---

## Phase 5: User Story 3 - Guardrail clause deduplicated (Priority: P3)

**Goal**: Reduce the 6 full restatements of the guardrail clause
(`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml` + real capital) to one
canonical statement, replacing the other 5 with the short cross-reference pattern already
proven at line 110 ("cf. Règles absolues") — without losing each paragraph's own
non-duplicated content (the specific mandate each paragraph states beyond the boundary clause).

**Independent Test**: for each of the 6 sites, only one full statement remains; the other 5
carry a one-line cross-reference instead; a line-by-line re-read confirms no mandate-specific
content (investigation autonomy, deployment autonomy, etc.) was dropped alongside the
deduplicated clause.

### Implementation for User Story 3

- [ ] T010 [US3] Re-identify the 6 full-restatement sites (`grep -n` the clause text in
      CLAUDE.md) and confirm which one stays canonical (line 15, "Ne jamais modifier son propre
      code ni les fichiers de garde-fous..." — the most general, already-referenced-elsewhere
      version) vs. line 8 (keep as-is: its *exception-scope* content is not a duplicate, only
      trim if it also restates the full clause redundantly)
- [ ] T011 [US3] In each of the remaining full-restatement sites (DOCTRINE D'AUTONOMIE's
      "Autonomie d'investigation & de proposition" and "Autonomie Totale de Déploiement et
      d'Auto-Correction" paragraphs), replace the repeated guardrail clause with a short
      cross-reference ("cf. Règles absolues"), preserving every other sentence of that
      paragraph unchanged (the mandate itself, not the boundary restatement, is what makes
      each paragraph non-duplicate)
- [ ] T012 [US3] Run `quickstart.md` step 5 (occurrence count check) AND manually re-read each
      edited paragraph side by side with its original wording — confirm no rule content was
      silently lost (per spec Edge Case: "preserve the more specific/complete wording, never
      silently pick the shorter one")

**Checkpoint**: All three user stories complete — CLAUDE.md now reflects its own governance
doctrine (HANDOFF relocation, no resolved-history residue, no duplicated guardrail clause).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and the single bundled commit.

- [ ] T013 Run `quickstart.md` step 1 (`wc -c CLAUDE.md`) — confirm the result sits at or below
      ~80000 bytes (research.md Decision 4 target); if not, identify what further trimming is
      needed before proceeding
- [ ] T014 Regenerate the constitution: `python3 scripts/generate-constitution.py`, then
      `git diff --stat .specify/memory/constitution.md` to confirm a new `source_digest`
      reflecting the CLAUDE.md edits
- [ ] T015 Run the full coherence suite (`cd packages/aria-core && python3 -m pytest
      tests/test_coherence.py -q`) — confirm zero regression beyond the intended changes
- [ ] T016 Commit `CLAUDE.md`, `docs/HANDOFF_INDEX.md`, and the regenerated
      `.specify/memory/constitution.md` together in a single commit (existing rule: constitution
      must never be committed separately from the CLAUDE.md edit that changed it)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on Setup (T001) only for T003's context; T002 has no
  dependency. Both BLOCK all user stories (baseline + snapshot must exist first).
- **User Stories (Phase 3-5)**: All depend on Foundational completion. Recommended order is
  P1 → P2 → P3 (matches spec priority: P1 is the largest safe win, P3 is the riskiest and
  benefits from the size problem already being resolved by P1/P2) — this is a risk-ordering
  recommendation from the spec, not a hard technical dependency between the stories themselves.
- **Polish (Phase 6)**: Depends on all three user stories being complete.

### Within Each User Story

- US1: T004 → T005 (must copy before removing) → T006 → T007 (verification after both edits)
- US2: T008 → T009
- US3: T010 → T011 → T012

### Parallel Opportunities

- T001 and T002 can run in parallel (different files, no shared state).
- T003 can run in parallel with T001/T002 (read-only extraction, no edit yet).
- Beyond that, most tasks touch the same file (`CLAUDE.md`) sequentially within and across
  stories — genuine parallelism is limited by design here, not an oversight.

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1 (T001) + Phase 2 (T002-T003).
2. Complete Phase 3 (US1: T004-T007) — CLAUDE.md already recovers ~4KB, its single largest
   relocatable block.
3. **STOP and VALIDATE**: run `quickstart.md` steps 1-3. If the operator wants to ship just
   this slice, it is already a complete, independently-verifiable improvement.

### Full Delivery (recommended)

1. Setup + Foundational.
2. US1 (P1) → verify → US2 (P2) → verify → US3 (P3) → verify.
3. Polish (Phase 6): final size check, constitution regeneration, full coherence suite,
   **one single commit** bundling CLAUDE.md + docs/HANDOFF_INDEX.md + constitution.md.

No parallel-team strategy applies — this is a single-file, single-session content edit; the
"team" section from the generic template is intentionally omitted.
