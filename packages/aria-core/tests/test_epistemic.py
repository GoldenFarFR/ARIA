"""Tests épistémique — YAML static + Groq calibré dynamique."""

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.knowledge.epistemic import (
    EPISTEMIC_DIRECT_SCORE,
    _parse_groq_calibrated,
    epistemic_direct_answer,
    epistemic_relevance_score,
    epistemic_static_answer,
    groq_calibrated_answer,
    resolve_calibrated_answer,
    search_epistemic,
)


def test_holding_static_match():
    reply, data = epistemic_static_answer("dexpulse est la holding", "fr")
    assert data.get("epistemic_static") is True
    assert data.get("match_id") == "holding-vs-dexpulse"
    assert "filiale" in reply.lower()


def test_world_question_not_in_yaml():
    reply, data = epistemic_static_answer("QUI est le president de la france", "fr")
    assert data.get("epistemic_static") is False
    assert reply is None


def test_earth_not_in_yaml():
    reply, data = epistemic_static_answer("la terre est-elle plate", "fr")
    assert data.get("epistemic_static") is False


def test_hype_policy_in_yaml():
    reply, data = epistemic_static_answer("100x garanti moon soon gem", "en")
    assert data.get("epistemic_static") is True
    assert "hype" in reply.lower()


def test_parse_groq_calibrated_president():
    raw = (
        "FAIT: VRAI\n"
        "REPONSE: Emmanuel Macron est président de la République française.\n"
        "P_VRAI: 0.97\n"
        "P_FAUX: 0.03\n"
        "RAISON: mandat en cours"
    )
    reply, data = _parse_groq_calibrated(raw, "fr")
    assert reply
    assert "Macron" in reply
    assert data["p_true"] == 0.97


@pytest.mark.asyncio
@patch("aria_core.llm.is_llm_configured", return_value=True)
@patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
async def test_resolve_calibrated_uses_groq_for_president(mock_chat, _mock_cfg):
    mock_chat.return_value = (
        "FAIT: VRAI\nREPONSE: Emmanuel Macron.\nP_VRAI: 0.96\nP_FAUX: 0.04\nRAISON: ok"
    )
    reply, data = await resolve_calibrated_answer("president de la france", "fr")
    assert data.get("groq_calibrated") is True
    assert "Macron" in reply
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
@patch("aria_core.llm.is_llm_configured", return_value=False)
async def test_groq_unavailable(_mock_cfg):
    reply, data = await groq_calibrated_answer("test", "fr")
    assert reply is None
    assert data.get("groq_calibrated") is False


def test_epistemic_direct_alias():
    reply, data = epistemic_direct_answer("dexpulse holding", "en")
    assert data.get("epistemic_static") is True


def test_relevance_score_holding():
    score = epistemic_relevance_score("dexpulse holding")
    assert score >= EPISTEMIC_DIRECT_SCORE


def test_search_static_only():
    matches = search_epistemic("terre plate", static_only=True)
    assert matches == [] or matches[0].score < EPISTEMIC_DIRECT_SCORE