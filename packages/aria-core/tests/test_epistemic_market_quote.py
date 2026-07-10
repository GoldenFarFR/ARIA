"""resolve_calibrated_answer intercepte les cotations connues AVANT le web/Groq --
incident réel corrigé le 10/07 (BTC/SOL cités ~30% sous leur vrai prix depuis une
page web périmée traitée comme « en direct »)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.knowledge.epistemic import resolve_calibrated_answer


@pytest.mark.asyncio
async def test_known_crypto_price_question_never_reaches_web_or_groq(monkeypatch):
    async def fake_quote(query, **kw):
        return "Cotation en direct (CoinGecko) : BTC : $62,083.96"

    monkeypatch.setattr(
        "aria_core.skills.market_quotes.resolve_known_asset_quote", fake_quote
    )

    with patch("aria_core.knowledge.epistemic.groq_calibrated_answer", new=AsyncMock()) as groq, \
         patch("aria_core.knowledge.web_verify.web_first_answer", new=AsyncMock()) as web:
        reply, meta = await resolve_calibrated_answer("prix du bitcoin ?", "fr", public=False)

    assert reply == "Cotation en direct (CoinGecko) : BTC : $62,083.96"
    assert meta.get("market_quote") is True
    assert meta.get("skip_web") is True
    groq.assert_not_called()
    web.assert_not_called()


@pytest.mark.asyncio
async def test_unrecognized_question_falls_through_to_normal_pipeline(monkeypatch):
    async def fake_quote_none(query, **kw):
        return None

    monkeypatch.setattr(
        "aria_core.skills.market_quotes.resolve_known_asset_quote", fake_quote_none
    )

    with patch(
        "aria_core.knowledge.epistemic.groq_calibrated_answer",
        new=AsyncMock(return_value=("Paris.", {"groq_calibrated": False})),
    ) as groq:
        reply, meta = await resolve_calibrated_answer("capitale de la france ?", "fr", public=False)

    assert reply == "Paris."
    groq.assert_called_once()
