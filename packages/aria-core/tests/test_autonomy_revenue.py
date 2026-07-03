import pytest

from aria_core.autonomy_revenue import (
    format_autonomy_status,
    revenue_autonomy_enabled,
    run_revenue_autonomy_cycle,
    _hours_since,
)


def test_revenue_autonomy_enabled_defaults(monkeypatch):
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_autonomous", True)
    monkeypatch.delenv("ARIA_REVENUE_AUTONOMY", raising=False)
    assert revenue_autonomy_enabled() is True

    monkeypatch.setattr(settings, "aria_autonomous", False)
    assert revenue_autonomy_enabled() is False

    monkeypatch.setattr(settings, "aria_autonomous", True)
    monkeypatch.setenv("ARIA_REVENUE_AUTONOMY", "off")
    assert revenue_autonomy_enabled() is False


def test_hours_since_none():
    assert _hours_since(None) > 1000


@pytest.mark.asyncio
async def test_cycle_disabled_when_autonomy_off(monkeypatch):
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_autonomous", False)
    result = await run_revenue_autonomy_cycle()
    assert result["ok"] is False
    assert "ARIA_REVENUE_AUTONOMY" in result["reason"]


@pytest.mark.asyncio
async def test_cycle_runs_market_scan(monkeypatch, tmp_path):
    from aria_core import autonomy_revenue as ar
    from aria_core import revenue_goals as rg

    monkeypatch.setattr(ar, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(rg, "LEDGER_PATH", tmp_path / "ledger.json")
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_autonomous", True)
    monkeypatch.setattr(settings, "aria_acp_provider_enabled", True)
    monkeypatch.setenv("ARIA_REVENUE_AUTONOMY", "true")

    async def fake_provider():
        return {"processed": 0, "actions": []}

    async def fake_scan():
        return {"ok": True, "source": "cache", "agent_count": 3, "market": {"categories": {}}}

    async def fake_launch(msg, lang="fr"):
        return "draft", {"promo": {}}

    async def fake_ping(lang="fr"):
        return ""

    monkeypatch.setattr("aria_core.skills.acp_cli.is_acp_available", lambda: True)
    monkeypatch.setattr("aria_core.skills.acp_provider_skill.run_provider_cycle", fake_provider)
    monkeypatch.setattr("aria_core.skills.acp_market_intelligence.run_market_scan", fake_scan)
    monkeypatch.setattr("aria_core.skills.acp_product_launch_skill.execute_product_launch", fake_launch)
    monkeypatch.setattr("aria_core.proactive.run_founder_ping", fake_ping)

    result = await run_revenue_autonomy_cycle(lang="fr")
    assert result["ok"] is True
    assert "market_scan" in result["actions"]
    assert result["market_scan"]["agents"] == 3


def test_format_autonomy_status_fr(monkeypatch, tmp_path):
    from aria_core import autonomy_revenue as ar
    from aria_core import revenue_goals as rg

    monkeypatch.setattr(ar, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(rg, "LEDGER_PATH", tmp_path / "ledger.json")
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_autonomous", True)

    out = format_autonomy_status("fr")
    assert "AUTONOMIE REVENU" in out
    assert "Mode : ON" in out
    assert "start-aria-autonomous.ps1" in out