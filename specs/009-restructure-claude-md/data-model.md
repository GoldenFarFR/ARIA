# Phase 1 Data Model: Restructure CLAUDE.md into dedicated docs/ files

No database or runtime data model applies — this feature restructures git-tracked text files.
Entities below are documentation artifacts and their relationships.

## Entities

### CLAUDE.md
- **Role**: Project's auto-loaded instruction file (read at every session start).
- **Attributes**: `byte_size` (must stay ≤ 102400, target ≤ ~80KB after this work), `sections[]`
  (Règles absolues, routeur, État actif blocks, backlog index, doctrine sections, HANDOFF list).
- **Validation**: `test_claude_md_stays_under_size_budget` (packages/aria-core/tests/test_coherence.py).
- **Change**: HANDOFF descriptions removed (name list + pointer kept); v8/v9/megacap residue
  paragraph trimmed to a pointer; guardrail clause deduplicated to one canonical statement + 6
  short cross-references.

### docs/HANDOFF_INDEX.md (new)
- **Role**: Dedicated index carrying the per-component HANDOFF description previously inline
  in CLAUDE.md.
- **Attributes**: one entry per `docs/HANDOFF_<component>.md` — `component_name`, `description`
  (verbatim from the original CLAUDE.md text), no additional narrative.
- **Relationship**: CLAUDE.md's shortened HANDOFF list points here with one line; each entry
  here points to its `docs/HANDOFF_<component>.md` file.
- **Validation** (new, only if genuinely needed during implementation — not assumed upfront):
  a coherence check that every `docs/HANDOFF_*.md` file has a matching entry here, mirroring
  the existing `test_handoff_file_indexed_in_claude_md` pattern.

### docs/HANDOFF_<component>.md (existing, ~30+ files)
- **Role**: Unchanged. Full per-incident detail, `[STATUS] Subject / Date / Problem / Solution`
  format, already established.
- **Relationship**: Indexed by `docs/HANDOFF_INDEX.md` after this change (was: indexed directly
  by CLAUDE.md).

### .specify/memory/constitution.md (generated)
- **Role**: Spec-kit's governance gate, auto-derived from CLAUDE.md's governance sections via
  `scripts/generate-constitution.py`.
- **Attributes**: `source_digest` (sha256 of the extracted CLAUDE.md sections).
- **Validation**: `test_constitution_is_in_sync_with_claude_md`.
- **Change**: Regenerated (never hand-edited) in the same commit as every CLAUDE.md edit in
  this feature — the digest will change as CLAUDE.md's governance-relevant sections are edited
  (dedup of Règles absolues / DOCTRINE D'AUTONOMIE directly affects the extracted content).

## State transitions

None — these are one-shot content edits, not stateful records. The only "transition" is
CLAUDE.md's byte size crossing from 102249 (in-extremis) to a target ≤ ~80KB (comfortable),
verified by re-running the two coherence tests after each edit, not inferred.
