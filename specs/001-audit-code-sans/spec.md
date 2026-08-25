# Feature Specification: Audit -- what was built but never delivered its expected result

**Feature Branch**: `001-audit-code-sans`
**Created**: 2026-08-25
**Status**: Draft
**Input**: Operator, 25/08 -- "je te l'ai surtout supprimé parce que ça me saoulait
d'avoir des rate limits depuis des mois alors qu'on a paramétré 50 fois le câblage
et que finalement les résultats n'ont jamais fonctionné, je m'attendais à avoir 50
smart wallets détectés prêts à être notés et on n'a jamais rien eu à part 50 rate
limits par seconde"

## Why this audit exists

The wallet-scoring removal (25/08) was framed as dead-code cleanup. That framing
was wrong, and the operator corrected it: the real failure was not the 3381 lines
of surplus code, it was that the mechanism ran for **months**, consumed a rate-limit
budget continuously, was rewired ~50 times, and **never produced a single qualified
smart wallet** -- its actual reason to exist.

Nothing in the repo would have caught that. Tests verified the code behaved as
written; no mechanism ever asked "is this thing delivering the outcome it was built
for?". A success criterion defined BEFORE building would have settled it in a week
instead of months.

This audit looks for other components in the same situation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find components that never delivered (Priority: P1)

As the operator, I want to know which running mechanisms have never produced their
intended output, so I can stop paying for them (API budget, RPC quota, context,
maintenance) or rebuild them on a sound basis.

**Why this priority**: this is the whole point of the audit, and the only part that
directly repays the wallet-scoring lesson. Everything else is secondary.

**Independent Test**: pick any audited component; its verdict must be backed by a
real measurement (row count, log count, DB query), never an opinion.

**Acceptance Scenarios**:
1. **Given** a component with a measurable expected output, **When** audited,
   **Then** the report states the real produced volume, the period covered, and
   whether it ever met its purpose.
2. **Given** a component whose expected output was never defined anywhere,
   **When** audited, **Then** the report says so explicitly -- an undefined success
   criterion is itself the finding, not a reason to skip the component.

### User Story 2 - Find orphan code and stale gates (Priority: P2)

As the operator, I want to know what code has no caller and which gates have been
off for a long time, so the repo stops carrying weight nobody reads.

**Why this priority**: cheaper to detect and lower risk than US1, but it is
cleanup, not insight. It must never crowd out US1.

**Acceptance Scenarios**:
1. **Given** a module with no importer outside its own tests, **When** audited,
   **Then** it is reported with the evidence (the grep that found no caller).
2. **Given** a gate disabled in prod, **When** audited, **Then** the report states
   how long it has been off, verified against the real container env.

### User Story 3 - Give every audited component a success criterion (Priority: P3)

As the operator, I want each surviving component to carry the measurable criterion
it should have had from day one, so this failure mode cannot silently repeat.

**Why this priority**: this is what makes the audit durable rather than a one-off
clean-up.

**Acceptance Scenarios**:
1. **Given** a component judged worth keeping, **When** audited, **Then** the report
   proposes a measurable success criterion and where it would be checked.

### Edge Cases

- A component that produced results ONCE then stopped: must be reported as a
  regression, distinct from "never worked".
- A component whose output feeds only a human read (Telegram/report): "delivered"
  means the operator actually saw it, not that a row exists.
- Shadow pockets and paper trading are DRAFTS by design (operator, 25/08): a
  retired pocket is not a failure and must not be reported as one. Only a pocket
  that never produced usable data at all counts here.
- A component whose expected volume is legitimately zero (a guardrail that never
  fires because nothing bad happened) must NOT be flagged -- absence of output is
  its success, not its failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every verdict MUST be backed by a real measurement taken during the
  audit (SQL count, log count, live gate read), never inferred from documentation.
- **FR-002**: The audit MUST proceed module by module, never loading the whole repo
  at once, so the context window is not exhausted mid-audit.
- **FR-003**: The audit MUST reuse existing mechanisms (Devil's Advocate reports,
  the 186 coherence tests, `system_issues`, watchdog logs) rather than re-deriving
  what they already establish.
- **FR-004**: The audit MUST NOT propose changes to guardrail files
  (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) or to real
  capital paths. Findings there are reported to the operator, never acted on.
- **FR-005**: Each finding MUST carry a concrete recommendation: remove, rebuild
  with a stated criterion, or keep with a newly defined criterion.
- **FR-006**: Findings MUST distinguish "never worked" from "worked then broke" --
  they have different causes and different fixes.
- **FR-007**: No strategy parameter, entry filter or invalidation bound may be
  written into this spec or its outputs (public repo) -- those go to `aria-ops`.

### Key Entities

- **Audited component**: a module, heartbeat cycle, gate, or pipeline with an
  identifiable purpose and an owner file.
- **Expected result**: what it was built to produce, in measurable terms.
- **Real result**: what it actually produced, measured during the audit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every component in scope has a verdict backed by a real measurement
  (0% verdicts based on reading documentation alone).
- **SC-002**: At least one component other than wallet-scoring is identified as
  never having delivered, OR the audit states with evidence that none exists.
- **SC-003**: Every "keep" verdict carries a measurable success criterion and the
  place it would be checked.
- **SC-004**: The audit completes without a context compaction losing its progress
  (tasks.md reflects real state at all times).

## Assumptions

- The audit is read-only: it produces findings and recommendations. Any removal is
  a separate, explicitly validated action -- retiring a mechanism is an operator
  decision (CLAUDE.md), never an autonomous conclusion of this audit.
- "Expected result" for components predating any written criterion will be inferred
  from their own docstring/HANDOFF and stated as an inference, not a fact.
