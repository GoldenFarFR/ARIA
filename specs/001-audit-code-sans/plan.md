# Implementation Plan: Audit -- what was built but never delivered its expected result

**Branch**: `001-audit-code-sans` | **Date**: 2026-08-25 | **Spec**: `specs/001-audit-code-sans/spec.md`

**Input**: Feature specification from `specs/001-audit-code-sans/spec.md`

## Summary

Audit every currently-wired ARIA mechanism against a single question: did it
ever produce the measurable output it was built for? The wallet-scoring
removal showed a mechanism can run for months, burn a rate-limit budget, get
rewired ~50 times, and pass every test while never once delivering its actual
purpose (a qualified smart wallet). This plan builds a repeatable, low-context
audit pass over the live gate list, not a one-off narrative report.

## Technical Context

**Language/Version**: Python 3.11 (matches `packages/aria-core`)

**Primary Dependencies**: none new. Reuses `aria_core.system_issues`,
`aria_core.bootstrap`, the prod SQLite reader (`aria.db`, read-only), `docker
logs`/`docker inspect` on `aria-api`, and existing HANDOFF files as the prior
art for each component.

**Storage**: read-only against `/opt/aria-data/aria.db` (`sqlite3 -readonly`,
per the VPS session norm) and the live container env (`docker inspect`). No
new schema, no write path -- the audit itself never mutates state.

**Testing**: pytest for the one piece of persistent logic this audit adds
(the scope registry / per-component checklist, if kept as code rather than a
plain markdown table -- decided in Phase 1). The audit's findings themselves
are validated by the measurement they cite, not by a test suite.

**Target Platform**: VPS session (this repo checkout), same environment as
every other Claude Code audit in this project.

**Project Type**: single audit pass producing a report + a per-component
checklist -- not a new application component.

**Performance Goals**: N/A (one-shot analytical task, not a runtime path).

**Constraints**: FR-002 (module by module, never the whole repo at once --
context-window discipline) and FR-004 (never touches guardrail files or real
capital, findings there are reported not acted on).

**Scale/Scope**: bounded by `docs/registre-automatisations.md`'s ~25
mechanisms plus the heartbeat-wired gates listed in the live env dump -- not
an unbounded repo-wide sweep.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Règles absolues** -- PASS. The audit is read-only by construction
  (Assumptions in spec.md): no guardrail file, no real-capital path, and no
  autonomous retirement decision. Every verdict must be backed by a live
  measurement (FR-001), which is the constitution's "vérifier avant
  d'affirmer" rule applied specifically to this task.
- **Permanent norms** -- PASS, and it is the norm this whole feature exists to
  enforce: aggregate over the full population before concluding (no sampling,
  no `LIMIT` verdicts), and never dump raw logs when a targeted `grep`/`COUNT`
  answers the question (FR-002).
- **DOCTRINE D'AUTONOMIE** -- PASS. Zero-Permission Policy covers code/tests/
  deploy; it does NOT cover retiring a mechanism outright (that is a durable
  architecture decision, same class as the wallet-scoring removal), so this
  plan keeps every "remove" verdict as a reported recommendation, never an
  autonomous action, consistent with the spec's own Assumptions section.
- **DOCTRINE D'INGÉNIERIE SYSTÉMIQUE** -- PASS, and structurally required:
  the audit itself must be staged (cheap filter first -- gate on/off, caller
  count, log volume -- before the expensive step -- reading a HANDOFF,
  reasoning about intent), never a brute-force read of every module.
- **Model & subagent policy** -- PASS. Cheap read-only scanning (grep for
  callers, log tailing) can go to a `researcher`/Explore subagent; the verdict
  and recommendation for each component stay in this session, never delegated
  wholesale.

No violations requiring Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/001-audit-code-sans/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output -- measurement method per component class
├── audit-scope.md        # Phase 1 output -- the bounded component list (data-model equivalent, no contracts/ needed: read-only feature, no API surface)
├── quickstart.md         # Phase 1 output -- how to re-run/extend the audit later
└── tasks.md              # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
specs/001-audit-code-sans/
├── spec.md               # this feature's spec (done)
├── plan.md               # this file
├── research.md           # Phase 0: per-component measurement method
├── audit-scope.md        # Phase 1: the bounded component list (data-model equivalent)
├── quickstart.md         # Phase 1: how to re-run/extend the audit later
└── tasks.md              # Phase 2 (/speckit-tasks) -- one task per audited component

docs/
└── HANDOFF_AUDIT_LIVRAISON.md   # final findings land here per the standing
                                  # HANDOFF-per-component convention (CLAUDE.md
                                  # router table), never stacked into CLAUDE.md
```

**Structure Decision**: no new application code. This is a documentation-only
feature under `specs/` plus one HANDOFF file for the findings, matching the
CLAUDE.md router table ("resolved history" / cross-cutting finding -> its own
HANDOFF, never inline in CLAUDE.md). If a component's audit needs a reusable
measurement helper (e.g. a generic "count callers of module X" check), it goes
in `scripts/`, not a new package -- to be confirmed per-component in tasks.md,
never assumed up front.

## Complexity Tracking

No entries -- the Constitution Check above found no violations to justify.
