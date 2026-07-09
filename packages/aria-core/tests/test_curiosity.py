"""run_curiosity_cycle : cycle de veille X + digest opportunités (#52).

Aucun réseau réel : fetch_curiosity_feed, request_approval, l'assess LLM et le stockage
cognitif sont tous mockés.
"""
from __future__ import annotations

import pytest

from aria_core import curiosity


@pytest.fixture(autouse=True)
def _configured_x(monkeypatch):
    from aria_core.testing import reload_test_settings

    reload_test_settings(monkeypatch, X_BEARER_TOKEN="test-bearer")


@pytest.fixture(autouse=True)
def _no_op_insight_pipeline(monkeypatch):
    """Neutralise le pipeline d'insights (purge/assess/add_knowledge/request_approval) --
    hors scope de ces tests, qui portent sur le digest opportunités."""
    async def _no_purge():
        return 0

    async def _reject_all(text, *, source="x_twitter"):
        from aria_core.knowledge.x_insight_relevance import InsightAssessment

        return InsightAssessment(
            store=False, pertinent=False, truth="n/a", reason="test", confidence=0.0, groq_used=False,
        )

    async def _noop_approval(*args, **kwargs):
        return None

    monkeypatch.setattr("aria_core.knowledge.cognitive.purge_placeholder_insights", _no_purge)
    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_x_insight_for_memory", _reject_all
    )
    monkeypatch.setattr(curiosity, "request_approval", _noop_approval)


def _fake_fetch(items):
    async def _fetch():
        return items

    return _fetch


@pytest.mark.asyncio
async def test_no_digest_without_notifier(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.fetch_curiosity_feed",
        _fake_fetch([{"topic": "@base", "text": "Someone should build an onchain agent standard."}]),
    )
    monkeypatch.setenv("ARIA_OPPORTUNITY_RADAR_ENABLED", "true")

    result = await curiosity.run_curiosity_cycle()  # pas de notifier
    assert result["opportunities"] == 0


@pytest.mark.asyncio
async def test_no_digest_when_gate_disabled(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.fetch_curiosity_feed",
        _fake_fetch([{"topic": "@base", "text": "Someone should build an onchain agent standard."}]),
    )
    monkeypatch.delenv("ARIA_OPPORTUNITY_RADAR_ENABLED", raising=False)

    sent = []

    async def _notifier(text):
        sent.append(text)

    result = await curiosity.run_curiosity_cycle(notifier=_notifier)
    assert result["opportunities"] == 0
    assert sent == []


@pytest.mark.asyncio
async def test_pushes_digest_when_enabled_and_opportunity_found(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.fetch_curiosity_feed",
        _fake_fetch([
            {"topic": "@base", "text": "Someone should build an onchain agent standard for x402."},
            {"topic": "@Whale_AI_net", "text": "gm"},  # bruit, ignoré
        ]),
    )
    monkeypatch.setenv("ARIA_OPPORTUNITY_RADAR_ENABLED", "true")

    sent = []

    async def _notifier(text):
        sent.append(text)

    result = await curiosity.run_curiosity_cycle(notifier=_notifier)
    assert result["opportunities"] == 1
    assert len(sent) == 1
    assert "Radar opportunités" in sent[0]
    assert "x:@base" in sent[0]


@pytest.mark.asyncio
async def test_no_digest_when_nothing_from_opportunity_handles(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.fetch_curiosity_feed",
        _fake_fetch([{"topic": "@some_random_account", "text": "Someone should build an onchain agent."}]),
    )
    monkeypatch.setenv("ARIA_OPPORTUNITY_RADAR_ENABLED", "true")

    sent = []

    async def _notifier(text):
        sent.append(text)

    result = await curiosity.run_curiosity_cycle(notifier=_notifier)
    assert result["opportunities"] == 0
    assert sent == []


@pytest.mark.asyncio
async def test_notifier_failure_does_not_crash_cycle(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.fetch_curiosity_feed",
        _fake_fetch([{"topic": "@base", "text": "Someone should build an onchain agent standard."}]),
    )
    monkeypatch.setenv("ARIA_OPPORTUNITY_RADAR_ENABLED", "true")

    async def _boom(text):
        raise RuntimeError("telegram down")

    result = await curiosity.run_curiosity_cycle(notifier=_boom)
    assert result["status"] == "ok"  # le cycle se termine proprement malgré l'échec d'envoi
