# Quickstart: resuming or extending this audit

For a future session (this one or a later one) picking this back up.

## Resuming

1. Read `tasks.md` first -- it holds the real per-component progress, not
   this file or `audit-scope.md` (per spec.md's SC-004: a context compaction
   must never lose progress, so state lives in `tasks.md`, not in a
   conversation thread).
2. For any task marked done, its verdict is a real measurement, not an
   opinion -- if you need to re-verify it, re-run the SAME query noted next
   to it in `tasks.md`, don't take the recorded number on faith past a few
   days old (gates and table contents move).
3. Pick up the next `[ ]` task in priority order (P1 before P2 before P3).

## Auditing one more component (extending the scope)

1. Find its class in `research.md` (heartbeat cycle / external client /
   gate / standalone process / guardrail) -- this tells you which source of
   truth to query, don't invent a new method per component.
2. Read its HANDOFF or CLAUDE.md entry FIRST for the stated purpose (the
   "expected result"). If none exists, that absence is itself the finding
   (spec.md Acceptance Scenario 2 under User Story 1) -- write it down as
   such rather than skipping the component.
3. Run the real measurement (SQL aggregate, `docker inspect`, log grep) --
   never conclude from a sample, always the full table (project-wide
   "lire toutes les lignes" norm).
4. Record verdict + evidence + recommendation (remove / rebuild with a
   criterion / keep with a criterion) as one row, same shape as the existing
   ones in `tasks.md`.
5. Add it to `audit-scope.md`'s table too, so the bound stays an accurate
   record of what's actually been covered.

## Where findings land

Per the CLAUDE.md router table, the final consolidated findings go to
`docs/HANDOFF_AUDIT_LIVRAISON.md` (one entry per component, `[STATUS] Subject
/ Date / Probleme / Solution` format) -- created once P1+P2 have real
verdicts, not before. CLAUDE.md itself gets, at most, a one-line pointer to
that HANDOFF plus this feature's index entry -- never the narrative.

## What this audit does NOT do

Per spec.md's Assumptions: no removal, no gate flip, no code change happens
as a side effect of a "remove" verdict here. That is always a separate,
explicitly validated action the operator takes after reading the finding --
same discipline as the wallet-scoring removal that seeded this feature.
