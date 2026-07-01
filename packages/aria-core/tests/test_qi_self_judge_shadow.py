import json

import pytest

from aria_core.qi_auto_judge import JudgeEvidence, earned_level
from aria_core.qi_judge_calibration import (
    compute_calibration_stats,
    is_aria_judge_promoted,
    record_shadow_run,
)
from aria_core.qi_self_judge_shadow import parse_shadow_json, run_shadow_calibration


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    from aria_core import capability_levels as cl
    from aria_core import qi_judge_calibration as cal

    monkeypatch.setattr(cl, "PROGRESS_PATH", tmp_path / "capability_progress.json")
    monkeypatch.setattr(cal, "CALIBRATION_PATH", tmp_path / "qi_judge_calibration.json")


def test_parse_shadow_json():
    raw = json.dumps({
        "codage": {"level": 10, "reason": "gaps résolus"},
        "business": {"level": 5, "reason": "ignored"},
        "autonomie": {"level": 7, "reason": "ship autonome"},
    })
    parsed = parse_shadow_json(raw)
    assert parsed["codage"][0] == 10
    assert parsed["business"][0] == 0
    assert parsed["autonomie"][0] == 7


def test_record_shadow_run_agreement():
    ev = JudgeEvidence(resolved_gaps_7d=5, health_ok=True, github_write=True)
    official = {cat: earned_level(cat, ev) for cat in (
        "codage", "social", "intelligence", "fiabilite", "autonomie", "business"
    )}
    shadow = dict(official)
    run = record_shadow_run(official, shadow, official_source="test")
    assert run["agreement_rate"] == 1.0
    stats = compute_calibration_stats()
    assert stats["runs_30d"] == 1


def test_promotion_requires_min_runs():
    ev = JudgeEvidence(resolved_gaps_7d=5, health_ok=True)
    official = {cat: earned_level(cat, ev) for cat in (
        "codage", "social", "intelligence", "fiabilite", "autonomie", "business"
    )}
    for _ in range(5):
        record_shadow_run(official, official, official_source="test")
    assert is_aria_judge_promoted(force=None) is False


def test_promotion_when_threshold_met(monkeypatch):
    from aria_core import qi_judge_calibration as cal

    monkeypatch.setattr(cal, "PROMOTION_MIN_RUNS", 3)
    ev = JudgeEvidence(resolved_gaps_7d=5, health_ok=True)
    official = {cat: earned_level(cat, ev) for cat in (
        "codage", "social", "intelligence", "fiabilite", "autonomie", "business"
    )}
    for _ in range(3):
        record_shadow_run(official, official, official_source="test")
    assert is_aria_judge_promoted(force=None) is True


@pytest.mark.asyncio
async def test_shadow_calibration_skips_without_llm(monkeypatch):
    monkeypatch.setattr(
        "aria_core.qi_self_judge_shadow.shadow_enabled",
        lambda: False,
    )
    ev = JudgeEvidence(resolved_gaps_7d=2)
    result = await run_shadow_calibration(ev)
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_shadow_calibration_records_match(monkeypatch):
    ev = JudgeEvidence(resolved_gaps_7d=5, health_ok=True, telegram_configured=True)
    official = {cat: earned_level(cat, ev) for cat in (
        "codage", "social", "intelligence", "fiabilite", "autonomie", "business"
    )}

    async def fake_verdict(_ev):
        return official

    monkeypatch.setattr(
        "aria_core.qi_self_judge_shadow.shadow_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "aria_core.qi_self_judge_shadow.request_shadow_verdict",
        fake_verdict,
    )
    monkeypatch.setattr(
        "aria_core.qi_self_judge_shadow.append_memory",
        lambda *a, **k: None,
    )
    result = await run_shadow_calibration(ev, official_source="test")
    assert result["skipped"] is False
    assert result["calibration"]["agreement_rate"] == 1.0