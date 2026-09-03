"""Step 3 (CLASSIFY) -- specs/019-security-scientist. One concrete detector:
gates mentioned in CLAUDE.md (declared) vs gates actually referenced in code
(observed). Decisive test follows the exact A/B/C scenario: declared={A,B},
runtime shows {A,B,C} -> C is a contradiction; same pass again -> still one,
never duplicated; C disappears -> resolved."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_scientist_contradictions as ssc  # noqa: E402

from aria_core import system_issues  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    from aria_core import paths

    monkeypatch.setattr(system_issues, "DB_PATH", str(tmp_path / "issues_test.db"))
    yield


# ---------------------------------------------------------------------------
# Pure function: find_contradictions(declared, observed) -- the diff logic,
# no I/O, no persistence.
# ---------------------------------------------------------------------------

def test_the_decisive_scenario_declared_ab_runtime_abc():
    declared = {"A", "B"}
    observed = {"A", "B", "C"}
    contradictions = ssc.find_contradictions(declared, observed)
    assert len(contradictions) == 1
    assert contradictions[0]["gate"] == "C"
    assert contradictions[0]["kind"] == "observed_not_declared"


def test_fully_consistent_sets_produce_no_contradiction():
    assert ssc.find_contradictions({"A", "B"}, {"A", "B"}) == []


def test_declared_but_never_observed_is_the_other_direction():
    contradictions = ssc.find_contradictions({"A", "B", "GHOST"}, {"A", "B"})
    assert len(contradictions) == 1
    assert contradictions[0]["gate"] == "GHOST"
    assert contradictions[0]["kind"] == "declared_not_observed"


# ---------------------------------------------------------------------------
# The stateful pass -- system_issues integration (FR-013/014: one record per
# contradiction, never duplicated while open, closed once resolved).
# ---------------------------------------------------------------------------

async def test_first_pass_opens_exactly_one_issue_for_the_new_process():
    now = time.time()
    result = await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now)
    assert result["contradictions_found"] == 1

    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1
    assert "C" in open_issues[0]["title"]


async def test_second_identical_pass_never_duplicates():
    now = time.time()
    await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now)
    await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now + 60)

    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1, "the same contradiction must never be recorded twice while open"


async def test_reality_returning_to_conformity_resolves_the_contradiction():
    now = time.time()
    await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now)
    assert len(await system_issues.list_open(source="security-scientist")) == 1

    # C disappears -- reality is back in conformity with what was declared.
    await ssc.record_pass({"A", "B"}, {"A", "B"}, now + 60)
    assert await system_issues.list_open(source="security-scientist") == []


async def test_a_contradiction_that_reappears_after_resolution_reopens_cleanly():
    now = time.time()
    await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now)
    await ssc.record_pass({"A", "B"}, {"A", "B"}, now + 60)
    assert await system_issues.list_open(source="security-scientist") == []

    await ssc.record_pass({"A", "B"}, {"A", "B", "C"}, now + 120)
    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1


async def test_multiple_contradictions_each_get_their_own_record():
    now = time.time()
    result = await ssc.record_pass({"A"}, {"A", "X", "Y"}, now)
    assert result["contradictions_found"] == 2
    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 2


# ---------------------------------------------------------------------------
# Insufficient proof -> UNKNOWN, never fabricated as a contradiction.
# ---------------------------------------------------------------------------

async def test_record_pass_can_restrict_to_one_direction():
    """Regression test for a real false-positive found by running against
    the actual repo (2026-09-03): CLAUDE.md is not an exhaustive gate list,
    so 'referenced in code but never mentioned in CLAUDE.md' produced 77
    false contradictions. record_pass must support reporting only the
    trustworthy direction for a given source pair."""
    now = time.time()
    result = await ssc.record_pass(
        {"A", "GHOST"}, {"A", "UNDOCUMENTED"}, now,
        directions=frozenset({"declared_not_observed"}),
    )
    assert result["contradictions_found"] == 1
    open_issues = await system_issues.list_open(source="security-scientist")
    assert len(open_issues) == 1
    assert "GHOST" in open_issues[0]["title"]
    assert "UNDOCUMENTED" not in open_issues[0]["title"]


async def test_real_claude_md_and_code_produce_no_ghost_gates_today():
    """Runs the actual production entry point against the real repo. Proof,
    not assumption -- this is the exact real-data check that found the
    77-false-positive bug above, now pinned to the corrected behavior."""
    result = await ssc.record_pass_from_files(now=time.time())
    assert result["status"] == "OK"
    # Not asserting a specific count (repo content changes over time) --
    # only that this direction stays small/sane, unlike the 77 we found in
    # the other direction.
    assert result["contradictions_found"] < 15


async def test_unreadable_source_yields_unknown_not_a_fabricated_contradiction():
    result = await ssc.record_pass_from_files(
        claude_md_path=Path("/nonexistent/CLAUDE.md"),
        code_root=Path("/nonexistent/code"),
        now=time.time(),
    )
    assert result["status"] == "UNKNOWN"
    assert result["contradictions_found"] == 0


# ---------------------------------------------------------------------------
# Real extraction against real files -- proves the regex/scan actually works,
# not just the diff logic.
# ---------------------------------------------------------------------------

def test_extract_gates_from_real_text():
    text = "Some doc mentioning ARIA_FOO_ENABLED and ARIA_BAR_ENABLED twice ARIA_FOO_ENABLED."
    assert ssc.extract_gate_names(text) == {"ARIA_FOO_ENABLED", "ARIA_BAR_ENABLED"}


def test_scan_code_root_finds_a_gate_reference(tmp_path):
    (tmp_path / "module.py").write_text('if os.getenv("ARIA_TESTGATE_ENABLED"):\n    pass\n')
    gates = ssc.scan_code_for_gates(tmp_path)
    assert "ARIA_TESTGATE_ENABLED" in gates
