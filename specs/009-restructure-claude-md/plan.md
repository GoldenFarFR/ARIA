# Implementation Plan: Restructure CLAUDE.md into dedicated docs/ files

**Branch**: `009-restructure-claude-md` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-restructure-claude-md/spec.md`

## Summary

CLAUDE.md exceeded its own 102400-byte size budget on 26/08 (emergency compaction already applied once). This is the durable fix: move the per-component HANDOFF description block (~30+ files) to a new dedicated index (`docs/HANDOFF_INDEX.md`), remove operational-sounding residue from fully-retired mechanisms (v8/v9/megacap), and deduplicate rules restated in both "Règles absolues" and "DOCTRINE D'AUTONOMIE ET DE TEMPÉRAMENT PROACTIF" — while leaving "État actif" sections, the backlog index, the guardrails, and the documentation router strictly in place inside CLAUDE.md, per the operator's explicit constraints. No new mechanism, no code change — pure content relocation and cleanup, verified against the two existing coherence tests.

## Technical Context

**Language/Version**: Markdown content (French/English per the repo's existing language split); governed by Python 3.11 tests

**Primary Dependencies**: `packages/aria-core/tests/test_coherence.py` (existing gates), `scripts/generate-constitution.py` (constitution regeneration)

**Storage**: N/A — git-tracked text files, no database

**Testing**: `pytest packages/aria-core/tests/test_coherence.py -k "claude_md or constitution or handoff or ghost_specs"`

**Target Platform**: N/A — documentation restructuring inside the existing ARIA monorepo

**Project Type**: single (docs-only change, no new source tree)

**Performance Goals**: N/A

**Constraints**: CLAUDE.md must stay comfortably under the 102400-byte cap after this work (target: enough margin for several future small additions, not a bare pass); zero information loss — every relocated paragraph must be traceable to its new destination

**Scale/Scope**: ~30+ HANDOFF component descriptions to relocate (~4KB block); a handful of v8/v9/megacap residue paragraphs to remove; two ~multi-KB doctrine sections to cross-check for duplication

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate (from constitution) | Status | Note |
|---|---|---|
| "Règles absolues" and the CLAUDE.md router table stay strictly inside CLAUDE.md | PASS | Spec FR-008 explicitly protects this; this plan touches neither. |
| "État actif" sections stay in place, edited in place, never relocated | PASS | Spec FR-006; confirmed out of scope for this feature. |
| Backlog technique index stays a compact index, never emptied to 100% | PASS | Spec FR-007; this work only verifies line length, doesn't touch the index structure. |
| HANDOFF doctrine (resolved history → HANDOFF file, never CLAUDE.md, not even summarized) | PASS (applied more thoroughly) | This feature exists to bring CLAUDE.md into fuller compliance with a rule it already states — not a new rule, not a bypass. |
| `test_claude_md_stays_under_size_budget` / `test_constitution_is_in_sync_with_claude_md` pass after every CLAUDE.md edit | PASS is the explicit deliverable | FR-009, SC-004. |
| Model & subagent policy — Red zone B (governance/architecture decisions warrant Opus for the design phase, even without real capital) | NOTED, not blocking | This plan was authored under Sonnet 5 xhigh (session default). Flagged to the operator in the completion report; work continues unless the operator asks to redo the design phase under Opus. |

No violation requiring justification: this feature's entire purpose is to restore CLAUDE.md's compliance with governance it already declares (size budget, HANDOFF relocation doctrine), not to relax any of it. Complexity Tracking table is omitted — nothing to justify.

## Project Structure

### Documentation (this feature)

```text
specs/009-restructure-claude-md/
├── plan.md              # This file
├── research.md           # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
└── tasks.md              # Phase 2 output (/speckit-tasks, not this command)
```

No `contracts/` directory: this feature exposes no external interface (API, CLI, UI) — it is a pure internal documentation restructuring.

### Source Code (repository root)

```text
CLAUDE.md                              # edited in place — trimmed, HANDOFF descriptions +
                                        # resolved-history residue removed, doctrine deduped
docs/HANDOFF_INDEX.md                  # NEW — receives the relocated per-component descriptions
docs/backlog-technique.md              # existing, referenced only, not restructured further here
.specify/memory/constitution.md        # regenerated (never hand-edited) after every CLAUDE.md change
packages/aria-core/tests/test_coherence.py  # existing gate tests, re-run; extended only if a
                                        # genuinely new invariant emerges (e.g. HANDOFF_INDEX
                                        # freshness check) — not assumed upfront
```

**Structure Decision**: Single project, no new source directories. This is a content-only change inside the existing ARIA monorepo — the "source" being restructured is documentation, not application code.

## Complexity Tracking

*No constitution violations — table intentionally omitted.*
