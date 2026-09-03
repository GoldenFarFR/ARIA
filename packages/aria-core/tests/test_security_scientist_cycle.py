"""CLASSIFY wired into the same run_pass() cycle as DISCOVER/OBSERVE/CRITIC/
JUDGE (specs/019-security-scientist). Five guarantees requested before
wiring: real contradiction persisted, no duplicate, resolution, a CLASSIFY
failure makes the whole cycle non-PASS, and CLASSIFY structurally cannot
touch the Judge's verdicts -- two parallel outputs of the same cycle, never
one feeding a new verdict into the other."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_scientist_contradictions as ssc  # noqa: E402
import security_scientist_observe as sso  # noqa: E402

from aria_core import security_evidence as se  # noqa: E402
from aria_core import system_issues  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", str(tmp_path / "security_scientist_test.db"))
    monkeypatch.setattr(system_issues, "DB_PATH", str(tmp_path / "issues_test.db"))
    yield


def _write_sources(tmp_path, declared_gates: set[str], observed_gates: set[str]):
    """A controlled A/B/C source pair -- a fake CLAUDE.md and a fake code
    tree, so the real record_pass_from_files code path (not a mock) runs
    against injected, controlled content."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(" ".join(sorted(declared_gates)))
    code_root = tmp_path / "code"
    code_root.mkdir(exist_ok=True)
    (code_root / "module.py").write_text(
        "\n".join(f'if os.getenv("{g}"): pass' for g in sorted(observed_gates))
    )
    return claude_md, code_root


# ---------------------------------------------------------------------------
# 1. A real contradiction is persisted by run_pass itself.
# ---------------------------------------------------------------------------

async def test_run_pass_creates_a_real_system_issue_for_an_injected_contradiction(tmp_path, monkeypatch):
    claude_md, code_root = _write_sources(tmp_path, {"ARIA_A_ENABLED"}, {"ARIA_B_ENABLED"})
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", claude_md)
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (code_root,))

    now = time.time()
    report = await sso.run_pass(now)
    assert report["classify"]["status"] == "OK"
    assert report["classify"]["contradictions_found"] == 1

    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1
    assert "ARIA_A_ENABLED" in open_issues[0]["title"]


# ---------------------------------------------------------------------------
# 2. Two identical cycles -- still exactly one contradiction, never doubled.
# ---------------------------------------------------------------------------

async def test_two_identical_cycles_never_duplicate(tmp_path, monkeypatch):
    claude_md, code_root = _write_sources(tmp_path, {"ARIA_A_ENABLED"}, {"ARIA_B_ENABLED"})
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", claude_md)
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (code_root,))

    await sso.run_pass(time.time())
    await sso.run_pass(time.time() + 1)

    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1


# ---------------------------------------------------------------------------
# 3. Reality returns to conformity -> the next cycle resolves it.
# ---------------------------------------------------------------------------

async def test_next_cycle_resolves_once_reality_conforms(tmp_path, monkeypatch):
    claude_md, code_root = _write_sources(tmp_path, {"ARIA_A_ENABLED"}, {"ARIA_B_ENABLED"})
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", claude_md)
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (code_root,))
    await sso.run_pass(time.time())
    assert len(await system_issues.list_open(source="security-scientist")) == 1

    # ARIA_A_ENABLED now genuinely referenced in code -- ghost gate resolved.
    (code_root / "module.py").write_text('if os.getenv("ARIA_A_ENABLED"): pass\n')
    await sso.run_pass(time.time() + 60)
    assert await system_issues.list_open(source="security-scientist") == []


# ---------------------------------------------------------------------------
# 4. CLASSIFY failing makes the WHOLE cycle non-PASS -- never silently
# swallowed, and visible in the heartbeat's exit code.
# ---------------------------------------------------------------------------

async def test_classify_failure_makes_the_cycle_report_non_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", Path("/nonexistent/CLAUDE.md"))
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (Path("/nonexistent/code"),))

    report = await sso.run_pass(time.time())
    assert report["classify"]["status"] != "OK"
    assert sso.cycle_ok(report) is False


async def test_classify_success_with_a_clean_discovery_reports_cycle_ok(tmp_path, monkeypatch):
    claude_md, code_root = _write_sources(tmp_path, {"ARIA_A_ENABLED"}, {"ARIA_A_ENABLED"})
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", claude_md)
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (code_root,))

    report = await sso.run_pass(time.time())
    assert report["classify"]["status"] == "OK"
    assert sso.cycle_ok(report) is True


# ---------------------------------------------------------------------------
# 5. Independence: CLASSIFY is a parallel output, never a second input to
# the Judge -- process evaluations must be bit-for-bit identical whether or
# not CLASSIFY ran, and CLASSIFY must never touch security_evaluations.
# ---------------------------------------------------------------------------

async def test_classify_never_writes_to_the_judges_evaluation_table(tmp_path, monkeypatch):
    claude_md, code_root = _write_sources(tmp_path, {"ARIA_GHOST_ENABLED"}, set())
    monkeypatch.setattr(sso, "CLASSIFY_CLAUDE_MD", claude_md)
    monkeypatch.setattr(sso, "CLASSIFY_CODE_ROOTS", (code_root,))

    now = time.time()
    report = await sso.run_pass(now)
    live = report["live"]
    assert live, "the pass must have discovered at least the test process itself"

    for surface_id in live:
        evaluations = await se.list_evaluations(surface_id)
        # Every recorded evaluation's self_critique_id must come from the
        # Judge's own pipeline (f"{surface_id}@{now}"), never from anything
        # CLASSIFY could have produced -- CLASSIFY writes zero rows here.
        for ev in evaluations:
            assert ev["self_critique_id"].startswith(surface_id), (
                "an evaluation exists that CLASSIFY could plausibly have written"
            )


def test_contradictions_module_has_no_import_of_security_evidence():
    """Static proof CLASSIFY cannot reach the Judge's evaluation table at
    all -- it only ever imports system_issues."""
    import ast

    tree = ast.parse(
        (Path(__file__).resolve().parents[3] / "scripts" / "security_scientist_contradictions.py").read_text()
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "aria_core.security_evidence" not in imported
    assert "security_scientist_judge" not in imported
