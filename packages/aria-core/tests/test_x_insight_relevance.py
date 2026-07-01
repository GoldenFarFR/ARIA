"""Tests filtre pertinence + triage Groq (mémoire cognitive X)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.knowledge.x_insight_relevance import (
    InsightAssessment,
    _parse_groq_triage,
    _prefilter_junk,
    assess_x_insight_for_memory,
    assess_x_insight_relevance,
    format_assessment_log,
)


def _groq_response(
    pertinent: str = "OUI",
    fait: str = "VRAI",
    conserver: str = "OUI",
    raison: str = "test",
) -> str:
    return f"PERTINENT: {pertinent}\nFAIT: {fait}\nCONSERVER: {conserver}\nRAISON: {raison}"


class TestPrefilterJunk:
    def test_empty(self):
        skip, reason = _prefilter_junk("")
        assert skip is True
        assert reason == "too_short"

    def test_short(self):
        skip, _ = _prefilter_junk("ok")
        assert skip is True

    def test_rt(self):
        skip, reason = _prefilter_junk("RT @foo: bar")
        assert skip is True
        assert reason == "retweet"

    def test_hype(self):
        skip, reason = _prefilter_junk("100x gem moon soon")
        assert skip is True
        assert reason == "off_topic_hype"

    def test_zhc_passes(self):
        skip, _ = _prefilter_junk("ZHC autonomie agent marketing")
        assert skip is False


class TestParseGroqTriage:
    def test_store_true(self):
        a = _parse_groq_triage(_groq_response())
        assert a.store is True
        assert a.pertinent is True
        assert a.truth == "true"
        assert a.groq_used is True

    def test_false_fact_rejected(self):
        a = _parse_groq_triage(
            _groq_response(pertinent="OUI", fait="FAUX", conserver="NON", raison="fait faux")
        )
        assert a.store is False
        assert a.truth == "false"

    def test_not_pertinent(self):
        a = _parse_groq_triage(
            _groq_response(pertinent="NON", fait="VRAI", conserver="NON", raison="hors scope")
        )
        assert a.store is False
        assert a.pertinent is False


class TestAssessXInsightForMemory:
    @pytest.mark.asyncio
    async def test_junk_skips_groq(self):
        a = await assess_x_insight_for_memory("ok")
        assert a.groq_used is False
        assert a.store is False

    @pytest.mark.asyncio
    @patch("aria_core.llm.is_llm_configured", return_value=True)
    @patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
    async def test_groq_store_true(self, mock_chat, _mock_cfg):
        mock_chat.return_value = _groq_response()
        a = await assess_x_insight_for_memory("ZHC autonomie agent marketing holding")
        assert a.groq_used is True
        assert a.store is True
        assert a.pertinent is True
        assert a.truth == "true"
        mock_chat.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("aria_core.llm.is_llm_configured", return_value=True)
    @patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
    async def test_groq_reject_false_fact(self, mock_chat, _mock_cfg):
        mock_chat.return_value = _groq_response(
            pertinent="OUI", fait="FAUX", conserver="NON", raison="fait faux"
        )
        a = await assess_x_insight_for_memory("ZHC autonomie agent marketing holding")
        assert a.store is False
        assert a.truth == "false"

    @pytest.mark.asyncio
    @patch("aria_core.llm.is_llm_configured", return_value=False)
    async def test_fallback_without_llm(self, _mock_cfg):
        a = await assess_x_insight_for_memory("ZHC autonomie agent marketing holding")
        assert a.groq_used is False
        assert a.store is True

    @pytest.mark.asyncio
    @patch("aria_core.llm.is_llm_configured", return_value=True)
    @patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
    async def test_groq_opinion_stored(self, mock_chat, _mock_cfg):
        mock_chat.return_value = _groq_response(
            pertinent="OUI", fait="OPINION", conserver="OUI", raison="avis utile"
        )
        a = await assess_x_insight_for_memory("ZHC autonomie agent marketing holding")
        assert a.store is True
        assert a.truth == "opinion"


class TestAssessXInsightRelevance:
    @pytest.mark.asyncio
    @patch("aria_core.llm.is_llm_configured", return_value=True)
    @patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
    async def test_backward_compat(self, mock_chat, _mock_cfg):
        mock_chat.return_value = _groq_response()
        store, label = await assess_x_insight_relevance("ZHC autonomie agent marketing holding")
        assert store is True
        assert "pertinent=True" in label


class TestFormatAssessmentLog:
    def test_groq_format(self):
        a = InsightAssessment(
            store=True,
            pertinent=True,
            truth="true",
            reason="ok",
            confidence=0.9,
            groq_used=True,
        )
        log = format_assessment_log(a)
        assert "pertinent=oui" in log
        assert "conserver=oui" in log