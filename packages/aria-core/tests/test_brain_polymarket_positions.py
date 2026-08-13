"""Deterministic (no-LLM) route for natural-language Polymarket questions (13/08).

Real incident: the operator typed "polymarket" then "je veut voir les paris en
cours" in Telegram. No ``INTENT_PATTERNS`` regex matched ("ANALYZE_PORTFOLIO"
only recognizes the bare word "positions?", never "polymarket"/"paris") -- the
message fell all the way through to the paid web-search fallback, which
returned an unrelated generic answer (FanDuel/DraftKings/Wikipedia) at real
cost ($0.00219, $0.52585 cumulative that month).

Same pattern as test_brain_manual_close_refusal.py -- checked BEFORE the
generic routing, in ``AriaBrain.process()``."""
from __future__ import annotations

import pytest

from aria_core.brain import AriaBrain


async def _noop_save(*a, **k):
    return None


@pytest.mark.asyncio
async def test_polymarket_bare_word_returns_the_real_portfolio_report(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    async def _fake_report():
        return "[FICTIF] Portefeuille Polymarket (paper trading)\nÉquité : $100,000"

    monkeypatch.setattr(
        "aria_core.polymarket_paper_trader.format_portfolio_report", _fake_report,
    )

    brain = AriaBrain()
    response = await brain.process("polymarket", lang="fr", public_mode=False)

    assert response.data.get("polymarket_positions") is True
    assert "Portefeuille Polymarket" in response.reply
    assert response.data.get("skip_web") is True


@pytest.mark.asyncio
async def test_paris_en_cours_phrasing_also_routes_to_the_real_portfolio(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    async def _fake_report():
        return "[FICTIF] Portefeuille Polymarket (paper trading)\nPositions ouvertes : 2"

    monkeypatch.setattr(
        "aria_core.polymarket_paper_trader.format_portfolio_report", _fake_report,
    )

    brain = AriaBrain()
    response = await brain.process("je veut voir les paris en cours", lang="fr", public_mode=False)

    assert response.data.get("polymarket_positions") is True
    assert "Positions ouvertes : 2" in response.reply


@pytest.mark.asyncio
async def test_polymarket_query_never_reaches_the_web_search_fallback(monkeypatch):
    """The real regression: the message must NEVER reach the paid web-search
    path (ACTU -- verified web sources)."""
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    async def _fake_report():
        return "[FICTIF] Portefeuille Polymarket (paper trading)"

    monkeypatch.setattr(
        "aria_core.polymarket_paper_trader.format_portfolio_report", _fake_report,
    )

    async def _fail_if_called(*a, **k):
        raise AssertionError("web search must never be reached for a polymarket question")

    monkeypatch.setattr("aria_core.knowledge.web_verify.web_enhance_calibrated", _fail_if_called)

    brain = AriaBrain()
    response = await brain.process("polymarket", lang="fr", public_mode=False)

    assert response.data.get("polymarket_positions") is True


@pytest.mark.asyncio
async def test_polymarket_public_visitor_never_reaches_the_positions_path(monkeypatch):
    """Admin-only doctrine (same as /polymarket, trade_status): a PUBLIC
    visitor must never trigger this path -- it would expose the real (fictive)
    portfolio with no access control."""
    import aria_core.brain as brain_mod

    async def _fail_if_called(*a, **k):
        raise AssertionError("_try_polymarket_positions_response must never be reached in public mode")

    monkeypatch.setattr(brain_mod.AriaBrain, "_try_polymarket_positions_response", _fail_if_called)

    async def _fake_repertoire_summary(lang):
        return "stub"

    async def _fake_truth_ledger(*a, **k):
        return "", {}

    monkeypatch.setattr(brain_mod, "get_repertoire_summary", _fake_repertoire_summary)
    monkeypatch.setattr(brain_mod, "truth_ledger_direct_answer", _fake_truth_ledger)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    brain = AriaBrain()
    response = await brain.process("polymarket", lang="fr", public_mode=True)
    assert response is not None


@pytest.mark.asyncio
async def test_unrelated_message_never_triggers_polymarket_positions(monkeypatch):
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: False)

    brain = AriaBrain()
    response = await brain.process("salut, comment ça va ?", lang="fr", public_mode=False)
    assert response.data.get("polymarket_positions") is not True
