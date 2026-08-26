# Phase 0 Research: Restructure CLAUDE.md into dedicated docs/ files

No `[NEEDS CLARIFICATION]` markers remained in the spec — this phase documents the concrete
decisions made after verifying the current state of CLAUDE.md directly (not guessed).

## Baseline measurements (verified 26/08, via `wc -c` / `grep` on the live file)

- CLAUDE.md current size: **102249 bytes**. The 100KB (102400-byte) budget test currently
  passes, but with only 151 bytes of margin — effectively "in extremis", exactly the situation
  the operator flagged as needing a durable fix, not another emergency compaction.
- The "Index of HANDOFF files by component" block: **4055 bytes** — the single largest
  relocatable block, matches the spec's User Story 1 estimate.
- The guardrail clause (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml` + real
  capital) appears **7 times**. Six of those restate the full clause; the seventh (line 110,
  inside "Zero-Permission Policy") already just says "cf. Règles absolues" — proof the
  short-pointer pattern already works elsewhere in the file and can be generalized.
- v8/v9/megacap: only **one** paragraph (the "Active state — pocket lineup" entry) narrates
  the retirement itself with operational-sounding detail (exact position counts, PnL figures)
  despite already pointing to `docs/HANDOFF_PIPELINE_MOMENTUM.md` for detail — this is the
  residue User Story 2 targets. The other four "v8" mentions are backlog index entries
  (#279, #371, #374, #377) using "v8" as shorthand for a still-referenced pattern (its LLM
  gates, its validation protocol), not a description of the retired pocket itself — these are
  NOT residue and must NOT be touched by this feature.

## Decision 1: Where the relocated HANDOFF descriptions go

**Decision**: New dedicated file `docs/HANDOFF_INDEX.md`.

**Rationale**: Matches the router table's own established pattern (one file per content
type, one-line pointer from CLAUDE.md) and keeps the concern distinct from adjacent files —
`docs/backlog-technique.md` is forward-looking (unbuilt leads), `docs/registre-automatisations.md`
is live-mechanism inventory; a HANDOFF index is neither — it's a lookup table for "has this
class of problem been solved before, and where."

**Alternatives considered**: Folding the descriptions into `docs/registre-automatisations.md`
— rejected, different purpose (active automations vs. resolved-problem lookup) and would
conflate two things a session searches for different reasons.

## Decision 2: What counts as "residue" vs. a legitimate still-relevant reference

**Decision**: Only the "Active state — pocket lineup" paragraph is residue (FR-004 target).
The four backlog-index mentions of "v8" (#279, #371, #374, #377) are NOT touched — they name
a conceptual pattern (anti-memorization LLM gates, validation protocol) still referenced as a
design precedent, independent of whether the concrete pocket still runs.

**Rationale**: The HANDOFF doctrine's own test ("resolved history — never in CLAUDE.md, not
even summarized") targets paragraphs that re-narrate an event already documented elsewhere —
not every string that happens to contain a retired component's name. Deleting a still-useful
conceptual reference to satisfy a text-matching heuristic would be an information loss the
spec's Edge Cases explicitly guard against (FR-010).

**Alternatives considered**: Blanket removal of every line containing "v8"/"v9"/"megacap" —
rejected, would silently break 4 backlog entries' meaning for zero size benefit (the backlog
lines are already index-compact, not the size problem).

## Decision 3: Deduplication method for Règles absolues vs. DOCTRINE D'AUTONOMIE

**Decision**: Keep the guardrail clause's fullest, most specific wording in "Règles absolues"
(the existing line 15 governs, plus the more detailed line 8 exception scope which is genuinely
different content, not a duplicate). Every other occurrence (lines 16, 17 inside DOCTRINE
D'AUTONOMIE) is replaced by the same short cross-reference pattern already proven at line 110
("cf. Règles absolues"), preserving each paragraph's own non-duplicated content (the specific
mandate — investigation autonomy, deployment autonomy — stays; only the repeated boundary
clause is deduplicated).

**Rationale**: This is a targeted, mechanical substitution (same clause, same 6 sites), not a
rewrite of the surrounding rules — lowest risk for the highest-risk story (P3), consistent
with the spec's own priority ordering (done last, after the safer size wins from Stories 1-2).

**Alternatives considered**: Merging the two sections entirely into one — rejected, out of
scope (the spec's Assumptions explicitly restrict this work to deduplicating the *repeated
clause*, not restructuring the sections' own organization).

## Decision 4: Size margin target

**Decision**: Target CLAUDE.md at or below **80KB** (roughly 80% of the 102400-byte cap),
not merely "under 102400" — matches spec SC-001's "comfortable margin, not barely under."

**Rationale**: The 4055-byte HANDOFF block plus the v8/v9/megacap residue (~900 bytes) plus
6 deduplicated guardrail-clause repetitions (each ~250-450 bytes recovered) totals roughly
7-8KB of direct recovery — comfortably reaching an ~93-94KB result even before any further
trimming, which already restores a multi-KB margin instead of the current 151-byte one.

**Alternatives considered**: No explicit numeric target, "just get comfortably under" —
rejected as unverifiable; a concrete percentage gives `quickstart.md` a pass/fail check.
