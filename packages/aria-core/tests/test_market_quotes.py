"""market_quotes — interception structurée des questions de prix sur des actifs
reconnus (crypto/devises), jamais un scrappage web. Incident réel corrigé (10/07) :
BTC/SOL cités ~30% sous leur vrai prix depuis une page web périmée."""
from __future__ import annotations

import pytest

from aria_core.services.coingecko import SimplePriceResult
from aria_core.services.forex import ExchangeRateResult
from aria_core.skills.market_quotes import detect_quote_question, resolve_known_asset_quote


# ----------------------------------------------------------------------
# detect_quote_question — fail-closed (ne devine jamais)
# ----------------------------------------------------------------------
def test_detects_crypto_price_question():
    match = detect_quote_question("à combien se situe le prix de l'eth solana et btc ?")
    assert match is not None
    assert match.kind == "crypto"
    assert set(match.ids) == {"ethereum", "solana", "bitcoin"}


def test_detects_single_crypto():
    match = detect_quote_question("prix du bitcoin ?")
    assert match.kind == "crypto"
    assert match.ids == ["bitcoin"]


def test_detects_forex_question():
    match = detect_quote_question("combien vaut le dollar en euro ?")
    assert match is not None
    assert match.kind == "forex"
    assert set(match.ids) == {"USD", "EUR"}


def test_no_price_keyword_returns_none():
    # Mentionne "bitcoin" mais aucun mot de prix -- fail-closed, ne devine pas l'intention.
    assert detect_quote_question("j'aime bien le bitcoin comme sujet") is None


def test_unknown_asset_returns_none():
    assert detect_quote_question("quel est le prix du café ?") is None


def test_single_currency_mention_not_enough_for_forex():
    # Une seule devise détectée : pas de paire -> pas de conversion possible, fail-closed.
    assert detect_quote_question("le prix du dollar aujourd'hui") is None


def test_empty_query_returns_none():
    assert detect_quote_question("") is None
    assert detect_quote_question(None) is None


# ----------------------------------------------------------------------
# resolve_known_asset_quote — crypto
# ----------------------------------------------------------------------
class _FakeCoinGecko:
    def __init__(self, result: SimplePriceResult):
        self._result = result
        self.called_with: list[str] | None = None

    async def get_simple_price(self, coin_ids, *, vs_currencies=None):
        self.called_with = coin_ids
        return self._result


class _FakeForex:
    def __init__(self, result: ExchangeRateResult):
        self._result = result
        self.called_with = None

    async def get_latest_rates(self, base, symbols):
        self.called_with = (base, symbols)
        return self._result


@pytest.mark.asyncio
async def test_resolve_crypto_success_formats_with_dollar_sign():
    fake = _FakeCoinGecko(
        SimplePriceResult(
            prices={"bitcoin": {"usd": 62083.96}, "ethereum": {"usd": 1796.10}},
            available=True,
        )
    )
    reply = await resolve_known_asset_quote("prix du btc et eth", coingecko_client=fake)
    assert reply is not None
    assert "BTC" in reply and "$62,083.96" in reply
    assert "ETH" in reply and "$1,796.10" in reply
    assert fake.called_with == ["bitcoin", "ethereum"]


@pytest.mark.asyncio
async def test_resolve_crypto_unavailable_returns_none_falls_through():
    fake = _FakeCoinGecko(SimplePriceResult(available=False, error="donnée indisponible"))
    reply = await resolve_known_asset_quote("prix du bitcoin", coingecko_client=fake)
    assert reply is None


@pytest.mark.asyncio
async def test_resolve_forex_success():
    fake = _FakeForex(
        ExchangeRateResult(base="USD", rates={"EUR": 0.92}, date="2026-07-10", available=True)
    )
    reply = await resolve_known_asset_quote("combien vaut le dollar en euro", forex_client=fake)
    assert reply is not None
    assert "0.9200 EUR" in reply
    assert "2026-07-10" in reply


@pytest.mark.asyncio
async def test_resolve_forex_unavailable_returns_none():
    fake = _FakeForex(ExchangeRateResult(base="USD", available=False, error="indisponible"))
    reply = await resolve_known_asset_quote("dollar en euro", forex_client=fake)
    assert reply is None


@pytest.mark.asyncio
async def test_non_quote_question_returns_none_immediately():
    # Aucun client ne doit même être sollicité pour une question hors périmètre.
    reply = await resolve_known_asset_quote("quel temps fait-il à paris ?")
    assert reply is None


@pytest.mark.asyncio
async def test_client_exception_degrades_to_none_not_raise():
    class _Boom:
        async def get_simple_price(self, coin_ids, *, vs_currencies=None):
            raise RuntimeError("network down")

    reply = await resolve_known_asset_quote("prix du bitcoin", coingecko_client=_Boom())
    assert reply is None
