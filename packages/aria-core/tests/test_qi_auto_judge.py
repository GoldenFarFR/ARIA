import pytest

from aria_core.qi_auto_judge import (
    JudgeEvidence,
    apply_earned_levels,
    earned_level,
    judge_codage,
    JUDGE_OUVRIER,
)


@pytest.fixture(autouse=True)
def isolated_progress(tmp_path, monkeypatch):
    from aria_core import capability_levels as cl

    monkeypatch.setattr(cl, "PROGRESS_PATH", tmp_path / "capability_progress.json")


def test_judge_codage_gem_crush_v50():
    ev = JudgeEvidence(gem_crush_version=50)
    lvl, reason = judge_codage(ev)
    assert lvl == 10
    assert "v50" in reason


def test_earned_levels_apply_multiple_axes():
    ev = JudgeEvidence(
        gem_crush_version=50,
        health_ok=True,
        health_commit="abc123",
        resolved_gaps_7d=3,
        telegram_configured=True,
        memory_entries=120,
        github_write=True,
    )
    events = apply_earned_levels(ev, source=JUDGE_OUVRIER)
    assert len(events) >= 5
    cats = {e["category"] for e in events}
    assert "codage" in cats
    assert "autonomie" in cats


def test_global_index_rises_after_judge():
    from aria_core.capability_levels import global_index

    ev = JudgeEvidence(gem_crush_version=40, health_ok=True, telegram_configured=True)
    apply_earned_levels(ev, source=JUDGE_OUVRIER)
    assert global_index() > 3.0


def test_business_not_forced_by_judge():
    ev = JudgeEvidence(gem_crush_version=99)
    lvl, _ = earned_level("business", ev)
    assert lvl == 0