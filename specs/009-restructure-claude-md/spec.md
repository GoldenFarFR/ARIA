# Feature Specification: Restructure CLAUDE.md into dedicated docs/ files

**Feature Branch**: `009-restructure-claude-md`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Restructurer CLAUDE.md pour alléger son contenu vers des fichiers dédiés dans docs/, sans perdre la fonction 'lu automatiquement à chaque session'. CLAUDE.md a dépassé son propre budget de taille (100 Ko) une première fois aujourd'hui (26/08), déjà corrigé ponctuellement (compaction d'urgence). Ce chantier vise une restructuration durable, pas un nouveau correctif ponctuel. Périmètre : sortir la description détaillée des 30+ fichiers HANDOFF vers un index dédié ; nettoyer les résidus d'historique déjà résolu (v8/v9/megacap) ; réduire les doublons entre 'Doctrine d'autonomie' et 'Règles absolues'. Contraintes : les sections 'État actif' restent dans CLAUDE.md éditées en place ; le backlog technique reste un index compact avec pointeur, jamais vidé à 100% ; les Règles absolues/garde-fous et le routeur de documentation restent strictement dans CLAUDE.md."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A new/compacted Claude Code session finds a HANDOFF fast without CLAUDE.md carrying its full description (Priority: P1)

When a session needs to check whether a "seen before" problem already has a HANDOFF file, it currently scans a ~4KB block inside CLAUDE.md itself. After this change, CLAUDE.md keeps only the list of component names with a one-line pointer to a dedicated index file, and the index file carries the per-file description.

**Why this priority**: this is the single largest identified block that can move out with zero loss of the "read automatically every session" property (CLAUDE.md still names every component; only the description moves).

**Independent Test**: grep CLAUDE.md for a component name (e.g. "HANDOFF_CHAINSTACK") — it is still found; the matched line points to the new index file; opening that file shows the original description text, unchanged.

**Acceptance Scenarios**:

1. **Given** CLAUDE.md's current "Index of HANDOFF files by component" section, **When** the restructuring is applied, **Then** CLAUDE.md contains only file names + a one-line pointer to the new index, and the new index file contains every original description verbatim.
2. **Given** a future new HANDOFF_<component>.md file, **When** it is created, **Then** the existing rule ("index it in the same commit") still applies, now pointing at the dedicated index file instead of CLAUDE.md directly.

---

### User Story 2 - Resolved historical residue no longer occupies space in CLAUDE.md (Priority: P2)

Mentions of pockets/mechanisms already fully removed (v8, v9, megacap) that no longer carry operational meaning are identified and removed from CLAUDE.md, per the HANDOFF doctrine already in place ("resolved history — never in CLAUDE.md, not even summarized").

**Why this priority**: real but bounded space recovery; lower risk than User Story 1 since it is pure deletion of dead content, not a move.

**Independent Test**: grep CLAUDE.md for the retired pocket names — no operational reference remains (a pointer to the HANDOFF entry that already documents the retirement is fine; a paragraph re-explaining it is not).

**Acceptance Scenarios**:

1. **Given** a mention of a fully-retired mechanism that adds no operational context today, **When** the cleanup is applied, **Then** the mention is removed and, if not already present, a one-line pointer to the relevant HANDOFF entry remains.

---

### User Story 3 - Duplicated guidance between "Doctrine d'autonomie" and "Règles absolues" is reduced to one statement (Priority: P3)

The "DOCTRINE D'AUTONOMIE ET DE TEMPÉRAMENT PROACTIF" section states it replaces an older section, but may still restate rules already covered in "Règles absolues".

**Why this priority**: smallest expected gain, and riskiest to get wrong (removing the wrong copy could silently drop a rule) — done last, after the size problem is already resolved by Stories 1-2.

**Independent Test**: for each rule that appears in both sections, only one canonical statement remains, with the other section (if relevant) carrying a one-line cross-reference instead of a restatement.

**Acceptance Scenarios**:

1. **Given** a rule stated in both sections with identical or near-identical wording, **When** the deduplication is applied, **Then** exactly one canonical version remains, and no rule is lost (verified by a diff-based review, not just visual skim).

### Edge Cases

- What happens when a future session searches CLAUDE.md for a HANDOFF component name using a partial/fuzzy match? → The name must still appear literally in CLAUDE.md (not only in the moved index file), so a plain grep on CLAUDE.md alone still finds it.
- What happens if the moved index file itself grows unbounded over time? → Out of scope for this spec (no size budget is being imposed on the new file today); flag as a future consideration, not a blocker.
- What happens if a rule identified as "duplicate" in User Story 3 actually has a subtle difference the reviewer missed? → The deduplication must preserve the more specific/complete wording, never silently pick the shorter one.
- What happens to `test_claude_md_stays_under_size_budget` and `test_constitution_is_in_sync_with_claude_md` during this work? → Both must be re-run and pass after every change; the constitution must be regenerated (`scripts/generate-constitution.py`) in the same commit as any CLAUDE.md edit.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: CLAUDE.md MUST keep every HANDOFF_<component>.md file name it currently lists (grep-able, unchanged), but MUST move the per-file description text to a new dedicated index file (e.g. `docs/HANDOFF_INDEX.md`).
- **FR-002**: The new HANDOFF index file MUST reproduce every original per-file description verbatim — no information loss, only relocation.
- **FR-003**: CLAUDE.md MUST retain a one-line pointer from the shortened HANDOFF list to the new dedicated index file.
- **FR-004**: CLAUDE.md MUST NOT retain operational-sounding paragraphs about mechanisms already fully retired (v8, v9, megacap) beyond what is needed to point to their HANDOFF entry, per the existing "resolved history → HANDOFF, never CLAUDE.md" rule.
- **FR-005**: Duplicated rules between "DOCTRINE D'AUTONOMIE ET DE TEMPÉRAMENT PROACTIF" and "Règles absolues" MUST be reduced to one canonical statement per rule, with the non-canonical location either removed or replaced by a one-line cross-reference.
- **FR-006**: "État actif" subsections MUST NOT be moved out of CLAUDE.md by this work — they stay in place, edited in place, per the existing router rule.
- **FR-007**: The backlog technique index (#NNN entries) MUST remain a compact index in CLAUDE.md (one line per item with a pointer to `docs/backlog-technique.md`) — this work MUST NOT empty it to zero, only verify no single line exceeds a reasonable index-line length (already partly done 26/08).
- **FR-008**: "Règles absolues" (guardrails, real capital, gates, kill-switch) and the CLAUDE.md router table (which content type goes where) MUST remain strictly inside CLAUDE.md, untouched by this relocation work.
- **FR-009**: After this work, `test_claude_md_stays_under_size_budget` MUST pass with a comfortable margin (not barely under the cap), and `test_constitution_is_in_sync_with_claude_md` MUST pass (constitution regenerated in the same commit).
- **FR-010**: Every piece of content moved out of CLAUDE.md MUST remain reachable via an explicit pointer — no silent deletion of information that was not already flagged as resolved/dead per FR-004.

### Key Entities

- **CLAUDE.md**: the project's own auto-loaded instruction file (git-tracked, read automatically at session start). Subject to `test_claude_md_stays_under_size_budget`.
- **docs/HANDOFF_INDEX.md** (new): dedicated index carrying the per-component HANDOFF description currently inline in CLAUDE.md.
- **docs/HANDOFF_<component>.md** (existing, ~30+ files): unchanged by this work, only how they are indexed changes.
- **.specify/memory/constitution.md**: generated artifact, must stay in sync with CLAUDE.md via `scripts/generate-constitution.py`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: CLAUDE.md's byte size sits comfortably under `test_claude_md_stays_under_size_budget`'s cap after this work (target: enough margin to absorb several future small additions without an emergency compaction, not just barely under).
- **SC-002**: Every HANDOFF component name that was previously findable via a plain-text search of CLAUDE.md remains findable the same way after the change.
- **SC-003**: Zero information loss: every relocated paragraph is verifiable, verbatim, in its new destination file.
- **SC-004**: `test_claude_md_stays_under_size_budget` and `test_constitution_is_in_sync_with_claude_md` both pass after the change, in the same commit.

## Assumptions

- The existing CLAUDE.md router table (content type → destination) already encodes the correct philosophy; this work applies it more thoroughly, it does not redesign it.
- "État actif" sections, the router table itself, and "Règles absolues" are explicitly out of scope for relocation (operator-confirmed constraint) — this spec's job is the HANDOFF index, resolved-history cleanup, and rule deduplication only.
- The backlog technique index (#NNN) already received a partial compaction pass earlier today (26/08); this work only verifies/finishes that pass, it does not redo it from scratch.
- No new automation/CI check is required beyond the two existing coherence tests (FR-009) — this is a content restructuring, not a new mechanism.
