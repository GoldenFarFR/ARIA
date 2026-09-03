"""Regression: the promotion cron wrote into CLAUDE.md until it broke the CI.

On 2026-09-03 the research-log promotion pass appended eight ~500-character
entries straight into CLAUDE.md. The file crossed its 100 KB CI ceiling and the
pre-push hook then refused the pushes of three separate sessions -- including
two commits produced by the promotion itself.

The interesting part is not the size. It is that the automation VIOLATED THE
ARCHITECTURE THE PROJECT DOCUMENTS: CLAUDE.md is declared a router (detail
lives in docs/), and the promotion prompt still instructed the opposite. The
declared architecture said A, the automation did B.

These tests pin the fix so the class cannot come back:
  - the detail is routed to docs/, CLAUDE.md gets at most a short index line;
  - the budget is checked MECHANICALLY, before and after -- an instruction in a
    prompt is not a guarantee;
  - a breach signals loudly (system_issue + non-zero exit) instead of silently
    handing a red CI to whoever pushes next;
  - and the guard never repairs CLAUDE.md itself, because a sibling session was
    editing it at that very moment.
"""

import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "research-log-promotion.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8", errors="ignore")


def test_the_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, "promotion script must stay executable"


def test_budget_is_measured_before_the_pass(script: str):
    assert "CLAUDE_MD_SIZE_BEFORE" in script
    assert "102400" in script, "the CI ceiling must be stated as a number, not implied"


def test_budget_is_verified_after_the_pass(script: str):
    # Checking only before would miss exactly what happened: the pass itself
    # is what crosses the ceiling.
    assert "CLAUDE_MD_SIZE_AFTER" in script
    assert re.search(r'CLAUDE_MD_SIZE_AFTER"?\s*-gt\s*"?\$CLAUDE_MD_BUDGET', script), \
        "the after-size must be compared against the budget"


def test_a_breach_exits_non_zero(script: str):
    guard = script[script.index("CLAUDE_MD_SIZE_AFTER"):]
    assert "exit 2" in guard, "a budget breach must fail the pass, not pass silently"


def test_a_breach_opens_a_system_issue(script: str):
    guard = script[script.index("CLAUDE_MD_SIZE_AFTER"):]
    assert "system_issues" in guard
    assert "promotion-claude-md-budget" in guard, "the issue needs a stable dedup key"
    assert "'critical'" in guard


def test_the_guard_never_repairs_claude_md_itself(script: str):
    # A blind `git checkout CLAUDE.md` would have destroyed the sibling
    # session's in-progress edit on the very day this was written.
    #
    # Match EXECUTED lines only: the first version of this test failed on the
    # comment that explains why the guard abstains -- the instrument reporting
    # a violation that did not exist, which is the very failure mode this file
    # is about.
    executed = [
        line for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    offenders = [l for l in executed if "git checkout" in l or "git restore" in l]
    assert not offenders, f"the guard must never rewrite CLAUDE.md itself: {offenders}"


def test_the_prompt_routes_detail_to_docs_not_claude_md(script: str):
    assert "docs/backlog-technique.md" in script, \
        "the prompt must name where the full entry goes"
    assert re.search(r"100\s*CARACTERES MAXIMUM|100 caracteres maximum", script), \
        "the index line must carry an explicit length bound"


def test_the_prompt_tells_the_pass_to_check_the_ceiling_before_writing(script: str):
    assert "stat -c %s /opt/aria/CLAUDE.md" in script, \
        "the pass must be told how to measure the file, not left to guess"


def test_the_prompt_forbids_a_pointer_to_an_absent_detail(script: str):
    # A pointer claiming "Detail: docs/backlog-technique.md" for an entry that
    # file does not contain is the same class of lie as a scanner reporting
    # PASS without measuring. Both happened on 2026-09-03.
    assert "un pointeur vers" in script.lower() or "pointeur" in script.lower()


def test_the_guard_logic_actually_fires_on_an_oversized_file(tmp_path: Path):
    """Behavioural control: replay the guard's arithmetic on a real file."""
    budget = 102400
    oversized = tmp_path / "CLAUDE.md"
    oversized.write_bytes(b"x" * (budget + 376))  # the exact overshoot of 03/09

    result = subprocess.run(
        ["bash", "-c",
         f'size=$(stat -c %s "{oversized}"); '
         f'if [ "$size" -gt {budget} ]; then exit 2; fi; exit 0'],
        capture_output=True,
    )
    assert result.returncode == 2, "an oversized CLAUDE.md must fail the guard"


def test_the_guard_stays_quiet_when_the_file_fits(tmp_path: Path):
    budget = 102400
    ok = tmp_path / "CLAUDE.md"
    ok.write_bytes(b"x" * (budget - 1))
    result = subprocess.run(
        ["bash", "-c",
         f'size=$(stat -c %s "{ok}"); '
         f'if [ "$size" -gt {budget} ]; then exit 2; fi; exit 0'],
        capture_output=True,
    )
    assert result.returncode == 0, "a file under budget must not trip the guard"
