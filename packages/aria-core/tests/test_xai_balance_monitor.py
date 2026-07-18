"""Surveillance solde x.ai — alerte à 1$, disjoncteur auto à 0,10$."""
from __future__ import annotations

import pytest

from aria_core import llm_circuit_breaker, xai_balance_monitor
from aria_core.paths import configure_data_dir
from aria_core.services.xai_billing import XaiBalance


def _patch_balance(monkeypatch, result: XaiBalance):
    async def fake_get_prepaid_balance():
        return result

    monkeypatch.setattr(
        "aria_core.xai_balance_monitor.get_prepaid_balance", fake_get_prepaid_balance,
    )


def _patch_configured(monkeypatch, value: bool):
    monkeypatch.setattr(
        "aria_core.xai_balance_monitor.xai_billing_configured", lambda: value,
    )


@pytest.mark.asyncio
async def test_skips_cleanly_without_credentials(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, False)

    result = await xai_balance_monitor.run_balance_check_cycle()

    assert result == {"skipped": "not_configured"}
    assert llm_circuit_breaker.is_armed() is False


@pytest.mark.asyncio
async def test_healthy_balance_does_nothing(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(balance_usd=5.0, available=True))

    result = await xai_balance_monitor.run_balance_check_cycle()

    assert result["action"] == "none"
    assert llm_circuit_breaker.is_armed() is False


@pytest.mark.asyncio
async def test_low_balance_alerts_once(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(balance_usd=0.8, available=True))
    alerts = []

    async def notifier(text):
        alerts.append(text)

    result1 = await xai_balance_monitor.run_balance_check_cycle(notifier=notifier)
    assert result1["action"] == "low_balance_alerted"
    assert len(alerts) == 1
    assert "0.80" in alerts[0] or "0.8" in alerts[0]

    # Deuxième passage, solde toujours bas -> pas de second spam.
    result2 = await xai_balance_monitor.run_balance_check_cycle(notifier=notifier)
    assert result2["action"] == "none"
    assert len(alerts) == 1
    assert llm_circuit_breaker.is_armed() is False


@pytest.mark.asyncio
async def test_balance_at_or_under_threshold_arms_breaker_and_notifies(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(balance_usd=0.05, available=True))
    alerts = []

    async def notifier(text):
        alerts.append(text)

    result = await xai_balance_monitor.run_balance_check_cycle(notifier=notifier)

    assert result["action"] == "circuit_breaker_armed"
    assert llm_circuit_breaker.is_armed() is True
    override = llm_circuit_breaker.get_override()
    assert override["provider"] == "openrouter"
    assert override["model"] == xai_balance_monitor.BREAKER_MODEL
    assert override["fallback_model"] == xai_balance_monitor.BREAKER_FALLBACK_MODEL
    assert len(alerts) == 1
    assert "OpenRouter" in alerts[0]


@pytest.mark.asyncio
async def test_already_armed_breaker_is_not_rearmed(tmp_path, monkeypatch):
    """Ne doit jamais réarmer/re-notifier en boucle une fois le disjoncteur déjà actif."""
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(balance_usd=0.02, available=True))
    llm_circuit_breaker.arm(
        provider="openrouter", model="anthropic/claude-sonnet-5", reason="deja arme",
    )
    alerts = []

    async def notifier(text):
        alerts.append(text)

    result = await xai_balance_monitor.run_balance_check_cycle(notifier=notifier)

    assert result["action"] == "none"
    assert alerts == []


@pytest.mark.asyncio
async def test_topup_resets_alert_flag(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(balance_usd=0.5, available=True))
    await xai_balance_monitor.run_balance_check_cycle()

    _patch_balance(monkeypatch, XaiBalance(balance_usd=10.0, available=True))
    reset_result = await xai_balance_monitor.run_balance_check_cycle()
    assert reset_result["action"] == "reset_after_topup"

    # Une nouvelle descente sous le seuil doit de nouveau alerter (pas figé "déjà alerté").
    _patch_balance(monkeypatch, XaiBalance(balance_usd=0.7, available=True))
    alerts = []

    async def notifier(text):
        alerts.append(text)

    result = await xai_balance_monitor.run_balance_check_cycle(notifier=notifier)
    assert result["action"] == "low_balance_alerted"
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_unavailable_balance_skips_cleanly(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    _patch_configured(monkeypatch, True)
    _patch_balance(monkeypatch, XaiBalance(available=False, error="panne reseau"))

    result = await xai_balance_monitor.run_balance_check_cycle()

    assert result["skipped"] == "unavailable"
    assert llm_circuit_breaker.is_armed() is False
