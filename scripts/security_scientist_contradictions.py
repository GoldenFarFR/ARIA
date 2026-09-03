#!/usr/bin/env python3
"""Step 3 (CLASSIFY) for the Security Scientist (specs/019) -- one concrete
detector, deliberately not a general reasoning engine: gates mentioned in
CLAUDE.md (declared) vs gates actually referenced in code (observed).

This is exactly the approved plan's first contradiction class ("ghost
gates: configured/injected every session, absent from the code -- 8 found
already"). Both sources are non-sensitive by construction: gate NAMES only,
never values (CLAUDE.md text and .py source are already public/versioned,
no secret is ever read).

Reports into aria_core.system_issues (source='security-scientist'), reusing
its own dedup-by-key discipline -- this module never invents a second
findings registry (FR-013/014)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "aria-core" / "src"))
from aria_core import system_issues  # noqa: E402

GATE_PATTERN = re.compile(r"\bARIA_[A-Z0-9_]+_ENABLED\b")

DEFAULT_CLAUDE_MD = Path(__file__).resolve().parents[1] / "CLAUDE.md"
DEFAULT_CODE_ROOTS = (
    Path(__file__).resolve().parents[1] / "packages" / "aria-core" / "src",
    Path(__file__).resolve().parents[1] / "vanguard" / "backend",
)


def extract_gate_names(text: str) -> set[str]:
    return set(GATE_PATTERN.findall(text))


def scan_code_for_gates(*roots: Path) -> set[str]:
    """Every ARIA_*_ENABLED name referenced anywhere under the given roots.
    Read errors on an individual file are skipped, never fabricated."""
    found: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                found.update(extract_gate_names(py_file.read_text(errors="ignore")))
            except OSError:
                continue
    return found


def find_contradictions(declared: set[str], observed: set[str]) -> list[dict]:
    """The diff, pure -- no I/O, no persistence. Each entry names which
    direction the contradiction runs, so 'declared but never observed' (a
    ghost gate) and 'observed but never declared' (an undocumented gate)
    are never conflated into one undifferentiated bucket."""
    contradictions = []
    for gate in sorted(declared - observed):
        contradictions.append({
            "kind": "declared_not_observed", "gate": gate,
            "detail": f"{gate} is mentioned in CLAUDE.md but never referenced in code",
        })
    for gate in sorted(observed - declared):
        contradictions.append({
            "kind": "observed_not_declared", "gate": gate,
            "detail": f"{gate} is referenced in code but never mentioned in CLAUDE.md",
        })
    return contradictions


def _dedup_key(c: dict) -> str:
    return f"gate-contradiction:{c['kind']}:{c['gate']}"


async def record_pass(
    declared: set[str], observed: set[str], now: float,
    *, directions: frozenset[str] = frozenset({"declared_not_observed", "observed_not_declared"}),
) -> dict:
    """One CLASSIFY pass: opens exactly one system_issues row per
    contradiction (system_issues.open_issue's own dedup_key already refuses
    a second open row for the same key -- FR-014), and closes any
    previously-open contradiction that reality no longer supports (FR-013's
    'recognize when a previously reported issue is resolved').

    `directions` lets a caller restrict which diff direction is actionable
    for its specific source pair -- see record_pass_from_files for why this
    matters: it is NOT always both directions that carry a real signal."""
    contradictions = [c for c in find_contradictions(declared, observed) if c["kind"] in directions]
    current_keys = {_dedup_key(c) for c in contradictions}

    for c in contradictions:
        await system_issues.open_issue(
            "security-scientist", f"Contradiction: {c['gate']} ({c['kind']})",
            detail=c["detail"], severity="warning", dedup_key=_dedup_key(c),
        )

    # NOTE: `directions` must stay the SAME across every pass for a given
    # source pair -- narrowing it between calls would spuriously close a
    # still-real contradiction from the now-excluded direction (it would
    # simply never appear in current_keys again). record_pass_from_files
    # below always calls with the same fixed direction, so this is safe in
    # practice, not merely assumed.
    previously_open = await system_issues.list_open(source="security-scientist")
    for issue in previously_open:
        if issue["dedup_key"] and issue["dedup_key"].startswith("gate-contradiction:") \
                and issue["dedup_key"] not in current_keys:
            await system_issues.close_issue(
                issue["id"], f"reality returned to conformity as of pass at {now}",
            )

    return {"status": "OK", "contradictions_found": len(contradictions)}


async def record_pass_from_files(
    *, claude_md_path: Path = DEFAULT_CLAUDE_MD,
    code_root: Path | tuple[Path, ...] = DEFAULT_CODE_ROOTS,
    now: float,
) -> dict:
    """Real-file entry point. Insufficient proof (a source cannot be read)
    yields UNKNOWN -- never a fabricated contradiction from a partial scan.

    Only reports `declared_not_observed` (a gate CLAUDE.md names but that no
    longer exists anywhere in code -- the approved plan's "ghost gate").
    `observed_not_declared` is deliberately excluded here, found empirically
    wrong for this source pair (2026-09-03): CLAUDE.md never claims to be an
    exhaustive list of every gate, only the notable/sensitive ones, so
    running the full diff against the real repo produced 77 "contradictions"
    that were really just gates CLAUDE.md never intended to mention -- noise,
    not signal. The `find_contradictions` function above stays a correct,
    general two-way diff; this caller is the one that knows which direction
    is trustworthy for ITS specific source pair."""
    try:
        declared = extract_gate_names(claude_md_path.read_text())
    except OSError:
        return {"status": "UNKNOWN", "contradictions_found": 0, "reason": "CLAUDE.md unreadable"}

    roots = code_root if isinstance(code_root, tuple) else (code_root,)
    if not any(r.exists() for r in roots):
        return {"status": "UNKNOWN", "contradictions_found": 0, "reason": "no code root readable"}
    observed = scan_code_for_gates(*roots)

    result = await record_pass(declared, observed, now, directions=frozenset({"declared_not_observed"}))
    result["status"] = "OK"
    return result


async def main() -> int:
    import json
    import time

    now = time.time()
    result = await record_pass_from_files(now=now)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "OK" else 2


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
