import pytest


@pytest.mark.asyncio
async def test_health_watch_ok_resets_streak(monkeypatch):
    from aria_core import health_watch as hw

    hw._FAIL_STREAK = 2

    async def fake_probe():
        return True, "ok"

    monkeypatch.setattr(hw, "_probe_health", fake_probe)  # noqa: async
    result = await hw.check_health_regression()
    assert result["ok"] is True
    assert result["streak"] == 0
    assert hw._FAIL_STREAK == 0


@pytest.mark.asyncio
async def test_health_watch_files_after_threshold(monkeypatch, tmp_path):
    """Après 3 échecs, health_watch signale (Telegram) -- ne délègue plus a
    un ouvrier externe et n'ouvre plus d'issue GitHub (10/07)."""
    from aria_core import health_watch as hw
    from aria_core import capability_gap as mod

    hw._FAIL_STREAK = 2

    async def fail_probe():
        return False, "timeout"

    monkeypatch.setattr(hw, "_probe_health", fail_probe)
    monkeypatch.setattr(mod, "_gaps_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_recently_filed", lambda _c: None)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    async def _noop_notify(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_notify_gap", _noop_notify)

    result = await hw.check_health_regression()
    assert result["ok"] is False
    assert result.get("gap", {}).get("status") == "logged"
    assert hw._FAIL_STREAK == 0


@pytest.mark.asyncio
async def test_qi_promote_no_notify_when_idle(monkeypatch):
    from aria_core import qi_promote as qp

    monkeypatch.setattr("aria_core.capability_levels.check_auto_completions", lambda: [])
    monkeypatch.setattr(qp, "count_resolved_gaps", lambda **k: 0)
    monkeypatch.setattr("aria_core.capability_levels.full_status", lambda lang: {
        "global_index": 0,
        "categories": {
            c: {"completed_level": 0, "next_level": 1, "auto_ready": False}
            for c in ("codage", "social", "intelligence", "fiabilite", "autonomie", "business")
        },
    })

    result = await qp.run_qi_promotion_check(lang="fr")
    assert result["notified"] is False


def test_append_pitfall_if_new(tmp_path, monkeypatch):
    from aria_core.knowledge import operator_runbook as orb

    pitfall_file = tmp_path / "operator_pitfalls.yaml"
    pitfall_file.write_text("pitfalls:\n  - id: existing\n    severity: low\n    lesson: x\n    fix: y\n", encoding="utf-8")
    monkeypatch.setattr(orb, "_PITFALLS_PATH", pitfall_file)
    orb._load.cache_clear()

    added = orb.append_pitfall_if_new({
        "id": "test_incident_auto",
        "severity": "high",
        "lesson": "test lesson",
        "fix": "test fix",
    })
    assert added is True
    dup = orb.append_pitfall_if_new({"id": "test_incident_auto", "lesson": "again", "fix": "again"})
    assert dup is False
