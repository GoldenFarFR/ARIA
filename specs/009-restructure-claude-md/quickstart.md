# Quickstart: Validate the CLAUDE.md restructuring

Run these checks after implementation (`/speckit-implement`), in order. Each must pass before
the feature is considered done — no step is a formality.

## Prerequisites

- Working tree at repo root (`/opt/aria`), `CLAUDE.md` and `docs/HANDOFF_INDEX.md` already edited.

## 1. Size check (SC-001)

```bash
wc -c CLAUDE.md
```

Expected: comfortably at or below ~80000 bytes (baseline before this work: 102249; hard cap:
102400 via `test_claude_md_stays_under_size_budget`). A result still above ~95000 means the
margin goal (research.md Decision 4) was not met — investigate before proceeding.

## 2. Every HANDOFF component name still findable in CLAUDE.md (SC-002)

```bash
for f in docs/HANDOFF_*.md; do
  name=$(basename "$f" .md)
  grep -q "$name" CLAUDE.md || echo "MISSING FROM CLAUDE.md: $name"
done
```

Expected: no output (every name still grep-able directly in CLAUDE.md, per FR-001/FR-003).

## 3. Zero information loss on the relocated block (SC-003)

```bash
git show HEAD~1:CLAUDE.md | awk '/^## Index of HANDOFF files by component/,/^## Format de réponse/' > /tmp/old_handoff_block.txt
diff <(grep -o '`docs/HANDOFF_[A-Z_]*\.md`.*' /tmp/old_handoff_block.txt | sort) \
     <(grep -o '`docs/HANDOFF_[A-Z_]*\.md`.*' docs/HANDOFF_INDEX.md | sort)
```

Expected: no meaningful diff — every original description line reappears verbatim in
`docs/HANDOFF_INDEX.md` (adjust the `git show` ref to whatever commit predates this feature's
changes on this machine).

## 4. v8/v9/megacap residue removed, backlog references untouched (User Story 2)

```bash
grep -n -iE '\bv8\b|\bv9\b|megacap' CLAUDE.md
```

Expected: the "Active state — pocket lineup" narrative paragraph is gone or reduced to a
pointer; the four backlog-index lines (#279, #371, #374, #377) referencing "v8" as a design
pattern are still present, unchanged (per research.md Decision 2 — these are not residue).

## 5. Guardrail clause deduplicated (User Story 3)

```bash
grep -c "fichiers garde-fous (\`permission_mode\`/\`wallet_guard\`/\`regles-uniques\`/\`config.toml\`)" CLAUDE.md
```

Expected: down from 6 full restatements to 1 canonical statement (the rest replaced by short
`cf. Règles absolues`-style cross-references, matching the existing line-110 pattern) — no
rule content lost, verified by reading each edited site, not just the count.

## 6. Coherence gates pass (FR-009, SC-004)

```bash
python3 scripts/generate-constitution.py
git diff --stat .specify/memory/constitution.md
cd packages/aria-core && python3 -m pytest tests/test_coherence.py -k "claude_md or constitution or handoff or ghost_specs" -q
```

Expected: constitution regenerated with a new `source_digest` reflecting the edits; all
matched tests pass. Commit the regenerated constitution together with the CLAUDE.md edit —
never separately (existing rule, `test_constitution_is_in_sync_with_claude_md`).

## 7. Full coherence suite, no regression

```bash
cd packages/aria-core && python3 -m pytest tests/test_coherence.py -q
```

Expected: full pass — this feature must not break any other coherence invariant (backlog
index format, HANDOFF indexing rule, no-ghost-specs check, etc.).
