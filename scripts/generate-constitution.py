#!/usr/bin/env python3
"""Generate `.specify/memory/constitution.md` from CLAUDE.md's own governance
sections -- never hand-written (25/08).

WHY THIS EXISTS, and why it is a generator rather than a document:
spec-kit's `plan`/`converge` commands read the constitution as their gate.
ARIA already has its governance in CLAUDE.md, enforced by `test_coherence.py`.
Keeping BOTH by hand guarantees they drift -- this project has three
documented cases of exactly that (gates described OFF in CLAUDE.md while
live ON in prod). A hand-copied constitution would not break any test; it
would diverge silently and eventually gate real-capital-adjacent code
against a stale rule. So the constitution is DERIVED, and
`test_constitution_is_in_sync_with_claude_md` fails the moment it is not.

Extraction is LITERAL: sections are copied verbatim, never summarised.
A summary is a hand-written artifact by another name and reintroduces the
same drift. The output is therefore large -- that is the intended tradeoff.

DETERMINISM: the output must be byte-identical across runs, or the sync test
would fail a day after it passed. No `date.today()`, no wall-clock anywhere.
The version is derived from a hash of the extracted source content, so it
changes if and only if the governance itself changed.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
CONSTITUTION = REPO_ROOT / ".specify" / "memory" / "constitution.md"

# Governance sections of CLAUDE.md, as EXPLICIT (start, end) heading bounds.
# Anything not listed here is deliberately NOT governance (active state, wired
# facts, vision, automations inventory) and must never leak in: a gate firing
# on transient state instead of a durable rule would block work for the wrong
# reason.
#
# Bounds are explicit rather than derived from heading level, because CLAUDE.md
# mixes levels -- the two DOCTRINE blocks are `#` while ordinary sections are
# `##`, so "next heading of level <= mine" does NOT close a doctrine and it
# silently swallowed the rest of the file (65KB instead of 1.5KB) when this was
# first written. Explicit bounds also fail LOUDLY on reorganisation, which is
# the point: losing a rule silently is far worse than a broken build.
GOVERNANCE_SECTIONS = (
    ("## Règles absolues", "## Watchword: ANTICIPATION"),
    ("## Permanent norms", "# DOCTRINE D'AUTONOMIE"),
    ("# DOCTRINE D'AUTONOMIE", "# DOCTRINE D'INGÉNIERIE"),
    ("# DOCTRINE D'INGÉNIERIE", "## Profil opérateur"),
    ("## Model & subagent policy", "## Deployment"),
)


def _find_heading(lines: list[str], prefix: str, start_at: int = 0) -> int:
    for i in range(start_at, len(lines)):
        if lines[i].startswith(prefix):
            return i
    return -1


def _extract_sections(text: str) -> list[tuple[str, str]]:
    """Return (heading, body) for each governance section, in file order.

    Raises rather than guessing if a bound is missing: a renamed heading must
    be a loud failure, never a constitution that quietly lost a principle.
    """
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []

    for start_prefix, end_prefix in GOVERNANCE_SECTIONS:
        start = _find_heading(lines, start_prefix)
        if start == -1:
            raise SystemExit(
                f"generate-constitution: start heading not found: {start_prefix!r}. "
                "A CLAUDE.md heading was renamed -- update GOVERNANCE_SECTIONS "
                "rather than letting the constitution lose a rule."
            )
        end = _find_heading(lines, end_prefix, start + 1)
        if end == -1:
            raise SystemExit(
                f"generate-constitution: end heading not found: {end_prefix!r} "
                f"(closing {start_prefix!r}). Update GOVERNANCE_SECTIONS."
            )
        body = "\n".join(lines[start + 1 : end]).strip("\n")
        sections.append((lines[start], body))

    return sections


def build_constitution(claude_md_text: str) -> str:
    sections = _extract_sections(claude_md_text)
    if not sections:
        raise SystemExit(
            "generate-constitution: no governance section found in CLAUDE.md -- "
            "a heading was renamed. Fix GOVERNANCE_HEADINGS rather than letting "
            "the constitution silently lose a rule."
        )

    source = "\n\n".join(f"{h}\n{b}" for h, b in sections)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]

    out: list[str] = []
    out.append("# ARIA Constitution")
    out.append("")
    out.append(
        "<!-- GENERATED FILE -- DO NOT EDIT BY HAND. "
        "Regenerate with `python3 scripts/generate-constitution.py`. "
        "Source of truth is CLAUDE.md; this file is derived from its governance "
        "sections so the two can never diverge silently "
        "(test_constitution_is_in_sync_with_claude_md enforces it). -->"
    )
    out.append("")
    out.append(
        "This constitution is the gate read by spec-kit's `plan` and `converge` "
        "commands. It is not an independent document: every principle below is "
        "copied verbatim from CLAUDE.md, which remains the single source of truth "
        "and is itself enforced by `packages/aria-core/tests/test_coherence.py`."
    )
    out.append("")
    out.append("## Core Principles")
    out.append("")

    for heading_line, body in sections:
        title = heading_line.lstrip("#").strip()
        out.append(f"### {title}")
        out.append("")
        out.append(body)
        out.append("")

    out.append("## Governance")
    out.append("")
    out.append(
        "This constitution supersedes no rule of CLAUDE.md -- it IS CLAUDE.md's "
        "governance, in the format spec-kit expects. Amendments are made in "
        "CLAUDE.md and propagated by regenerating this file in the same commit. "
        "Editing this file directly is a defect: the next regeneration silently "
        "discards the edit, and the sync test fails in the meantime."
    )
    out.append("")
    out.append(
        "Two bounds that no spec-kit workflow may relax, restated here because "
        "this file is what the planning gate reads: guardrail files "
        "(`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) and "
        "real capital always require an explicit operator \"ok\"; destructive git "
        "operations are never autonomous."
    )
    out.append("")
    out.append(f"**Source digest**: `{digest}` (sha256 of the extracted sections)")
    out.append("")
    return "\n".join(out)


def main() -> int:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    generated = build_constitution(text)

    check_only = "--check" in sys.argv
    current = CONSTITUTION.read_text(encoding="utf-8") if CONSTITUTION.exists() else None

    if check_only:
        if current != generated:
            print(
                "constitution OUT OF SYNC with CLAUDE.md -- run "
                "`python3 scripts/generate-constitution.py`",
                file=sys.stderr,
            )
            return 1
        print("constitution in sync")
        return 0

    CONSTITUTION.parent.mkdir(parents=True, exist_ok=True)
    CONSTITUTION.write_text(generated, encoding="utf-8")
    print(f"wrote {CONSTITUTION.relative_to(REPO_ROOT)} ({len(generated.encode())} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
