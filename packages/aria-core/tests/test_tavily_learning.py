"""Cycle d'auto-formation continue via Tavily -- 22/07.

Comble le trou laissé par l'API X officielle coupée : réutilise le pipeline
curiosity existant (triage/pending/approbation), seule la source change.
Aucun réseau réel : ``tavily_client.search``, le triage LLM, ``add_knowledge``
et ``request_approval`` sont tous mockés."""
from __future__ import annotations

import pytest

from aria_core.knowledge.x_insight_relevance import InsightAssessment
from aria_core.services import tavily
from aria_core.services.tavily import TavilyResult
from aria_core.skills import tavily_learning


def _patch_search(monkeypatch, fn):
    """Patche la CLASSE, jamais l'instance singleton ``tavily_client`` -- un patch sur
    l'instance forcerait ``monkeypatch`` à restaurer en RÉAFFECTANT (jamais en
    supprimant) au teardown, ce qui fige la vraie méthode en attribut d'instance
    permanent et masque tout futur patch de classe pour le reste de la session (fuite
    réelle trouvée le 22/07, cassait ``test_web_verify_freshness.py`` en suite complète)."""
    monkeypatch.setattr(type(tavily.tavily_client), "search", staticmethod(fn))


@pytest.fixture(autouse=True)
def _isolated_cursor_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tavily_learning, "DB_PATH", str(tmp_path / "tavily_learning_test.db"))
    yield


@pytest.fixture(autouse=True)
def _isolated_budget_db(tmp_path, monkeypatch):
    from aria_core.services import tavily_budget

    monkeypatch.setattr(tavily_budget, "DB_PATH", str(tmp_path / "tavily_budget_test.db"))
    yield


@pytest.fixture(autouse=True)
def _configured_and_enabled(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("ARIA_TAVILY_LEARNING_ENABLED", "true")


def _reject_assessment():
    return InsightAssessment(
        store=False, pertinent=False, truth="n/a", reason="test_reject", confidence=0.0, groq_used=False,
    )


def _accept_assessment(confidence=0.8):
    return InsightAssessment(
        store=True, pertinent=True, truth="true", reason="test_accept", confidence=confidence, groq_used=True,
    )


def _fake_search(result: TavilyResult):
    async def _search(*args, **kwargs):
        return result

    return _search


@pytest.mark.asyncio
async def test_skipped_when_gate_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_TAVILY_LEARNING_ENABLED", raising=False)
    result = await tavily_learning.run_tavily_learning_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_skipped_when_tavily_not_configured(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    result = await tavily_learning.run_tavily_learning_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_skipped_when_budget_exhausted(monkeypatch):
    from aria_core.services import tavily_budget

    # Épuise le budget mensuel avant de lancer le cycle.
    await tavily_budget.record_spend(credits=tavily_budget.MONTHLY_CAP_CREDITS)

    result = await tavily_learning.run_tavily_learning_cycle()
    assert result == {"outcome": "budget_exhausted"}


@pytest.mark.asyncio
async def test_rejects_insight_when_triage_declines(monkeypatch):
    async def _reject(text, *, source="x_twitter"):
        return _reject_assessment()

    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_x_insight_for_memory", _reject
    )
    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_market_knowledge_for_memory", _reject
    )
    _patch_search(
        monkeypatch,
        _fake_search(
            TavilyResult(
                query="q", snippets=[("some long enough snippet text here", "url", None)], available=True,
            )
        ),
    )

    requested = []

    async def _fake_request_approval(action, desc):
        requested.append((action, desc))
        return None

    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.request_approval", _fake_request_approval
    )

    result = await tavily_learning.run_tavily_learning_cycle()
    assert result["outcome"] == "ok"
    assert result["insights"] == 0
    assert requested == []


@pytest.mark.asyncio
async def test_stores_insight_and_requests_approval_when_triage_accepts(monkeypatch):
    async def _accept(text, *, source="x_twitter"):
        return _accept_assessment()

    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_x_insight_for_memory", _accept
    )
    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_market_knowledge_for_memory", _accept
    )
    _patch_search(
        monkeypatch,
        _fake_search(
            TavilyResult(
                query="q",
                snippets=[("some long enough snippet text here", "https://x.com/foo", None)],
                available=True,
            )
        ),
    )

    stored = []

    async def _fake_add_knowledge(source, topic, content, confidence=0.5, approved=False):
        stored.append({"source": source, "topic": topic, "content": content})

        class _Item:
            id = "abc123"

        return _Item()

    async def _fake_get_pending(limit=3):
        return []

    requested = []

    async def _fake_request_approval(action, desc):
        requested.append((action, desc))
        return None

    monkeypatch.setattr("aria_core.knowledge.cognitive.add_knowledge", _fake_add_knowledge)
    monkeypatch.setattr("aria_core.knowledge.cognitive.get_pending", _fake_get_pending)
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.request_approval", _fake_request_approval
    )

    result = await tavily_learning.run_tavily_learning_cycle()
    assert result["outcome"] == "ok"
    # 1 recherche X (accept) + 1 recherche sujet (accept) = 2 insights stockés.
    assert result["insights"] == 2
    assert len(stored) == 2
    assert {s["source"] for s in stored} == {"tavily_x", "tavily_learning"}
    assert len(requested) == 1
    assert requested[0][0] == "learn_knowledge"


@pytest.mark.asyncio
async def test_search_failure_does_not_crash_cycle(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    _patch_search(monkeypatch, _boom)

    result = await tavily_learning.run_tavily_learning_cycle()
    assert result["outcome"] == "ok"
    assert result["insights"] == 0


@pytest.mark.asyncio
async def test_round_robin_advances_through_handles(monkeypatch):
    monkeypatch.setattr(
        "aria_core.knowledge.x_watchlist.all_curiosity_handles",
        lambda: ("alice", "bob", "carol"),
    )
    _patch_search(
        monkeypatch,
        _fake_search(TavilyResult(query="q", snippets=[], available=False, error="no results")),
    )

    picked_handles = []
    for _ in range(4):
        result = await tavily_learning.run_tavily_learning_cycle()
        picked_handles.append(result["picked"].get("x_handle"))

    # Boucle après 3 comptes : alice, bob, carol, alice.
    assert picked_handles == ["alice", "bob", "carol", "alice"]


@pytest.mark.asyncio
async def test_next_item_returns_none_for_empty_list():
    assert await tavily_learning._next_item("empty_list", []) is None
