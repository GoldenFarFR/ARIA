"""Client Blockscout Pro (x402) -- aucun appel réseau réel, x402_executor.
fetch_paid_resource mocké directement (même patron que test_twitsh.py). Schéma
de réponse réel capturé en conditions réelles (21/07, 2 paiements de vérification,
WETH Base)."""
from __future__ import annotations

import pytest

from aria_core.services import blockscout_x402


class _FakeResult:
    def __init__(self, *, status, body=b"", reason="", amount_usd=0.0):
        self.status = status
        self.body = body
        self.reason = reason
        self.amount_usd = amount_usd


# Forme réelle observée (21/07) -- tronquée à 2 holders, mais tous les champs
# réellement présents sur la réponse réelle sont représentés (labels d'entité,
# is_verified/is_scam/reputation).
_REAL_HOLDERS_BODY = (
    b'{"items":[{"address":{"hash":"0xA0b8...Router","name":"UniswapV3Pool",'
    b'"is_contract":true,"is_verified":true,"is_scam":false,"reputation":"ok",'
    b'"metadata":{"tags":[{"name":"UniswapV3Pool"},{"name":"DEX"}]}},'
    b'"value":"1200000000000000000"},'
    b'{"address":{"hash":"0xDeaD...beef","name":null,"is_contract":false,'
    b'"is_verified":false,"is_scam":false,"reputation":null,"metadata":{}},'
    b'"value":"50000000000000000"}]}'
)


@pytest.mark.asyncio
async def test_get_token_holders_parses_real_response_shape(monkeypatch):
    async def fake_fetch(url, *, resource, provider, balance_fn, pay_fn, **kwargs):
        assert url == "https://api.blockscout.com/8453/api/v2/tokens/0xWETH/holders"
        assert resource == "token-holders"
        assert provider == "blockscout"
        assert kwargs.get("timeout") == blockscout_x402._HOLDERS_TIMEOUT_S
        return _FakeResult(status="ok", body=_REAL_HOLDERS_BODY, amount_usd=0.002)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    holders = await blockscout_x402.get_token_holders_x402("0xWETH", chain="base")

    assert len(holders) == 2
    assert holders[0]["holder_address"] == "0xA0b8...Router"
    assert holders[0]["is_contract"] is True
    assert holders[0]["is_verified"] is True
    assert "UniswapV3Pool" in holders[0]["tags"]
    assert holders[0]["value"] == "1200000000000000000"
    assert holders[1]["holder_name"] is None
    assert holders[1]["tags"] == []


@pytest.mark.asyncio
async def test_get_token_holders_uses_extended_timeout(monkeypatch):
    """Bug réel corrigé le 21/07 (x402_executor) : le défaut 12s ne suffit pas au
    règlement observé (~28-45s) -- ce test verrouille que le timeout étendu est
    bien transmis, pas juste documenté en commentaire."""
    seen = {}

    async def fake_fetch(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return _FakeResult(status="ok", body=_REAL_HOLDERS_BODY)

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    await blockscout_x402.get_token_holders_x402("0xWETH", chain="base")

    assert seen["timeout"] == 75.0


@pytest.mark.asyncio
async def test_get_token_holders_empty_contract_no_call(monkeypatch):
    async def _fail_if_called(*a, **k):
        raise AssertionError("ne doit jamais être appelé, contrat vide")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _fail_if_called)

    assert await blockscout_x402.get_token_holders_x402("") == []
    assert await blockscout_x402.get_token_holders_x402("   ") == []


@pytest.mark.asyncio
async def test_get_token_holders_unknown_chain_no_call(monkeypatch):
    async def _fail_if_called(*a, **k):
        raise AssertionError("ne doit jamais être appelé, chaîne non couverte")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _fail_if_called)

    assert await blockscout_x402.get_token_holders_x402("0xWETH", chain="solana") == []


@pytest.mark.asyncio
async def test_get_token_holders_blocked_returns_empty(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="blocked", reason="plafond hebdomadaire x402 dépassé")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await blockscout_x402.get_token_holders_x402("0xWETH") == []


@pytest.mark.asyncio
async def test_get_token_holders_exception_never_raises(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", _raise)

    assert await blockscout_x402.get_token_holders_x402("0xWETH") == []


@pytest.mark.asyncio
async def test_get_token_holders_unreadable_body_returns_empty(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="ok", body=b"not json at all")

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await blockscout_x402.get_token_holders_x402("0xWETH") == []


@pytest.mark.asyncio
async def test_get_token_holders_missing_items_returns_empty(monkeypatch):
    async def fake_fetch(url, **kwargs):
        return _FakeResult(status="ok", body=b'{"unexpected": "shape"}')

    monkeypatch.setattr("aria_core.x402_executor.fetch_paid_resource", fake_fetch)

    assert await blockscout_x402.get_token_holders_x402("0xWETH") == []
