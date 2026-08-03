"""Signal 'concentration de vente' -- vente concentrée sur un acteur unique
vs distribution large de prise de profit (03/08, cas C-MEM)."""
from __future__ import annotations

import pytest

from aria_core.skills.sell_distribution import (
    SellDistributionFacts,
    gather_sell_distribution_facts,
    judge_sell_distribution,
)

TOKEN = "0x" + "a" * 40


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_sell_distribution(SellDistributionFacts(available=False, error="clé absente"))
    assert v.signal == "unknown"


def test_too_few_sellers_is_neutral():
    v = judge_sell_distribution(
        SellDistributionFacts(sellers_examined=2, total_sold_usd=100.0, top_seller_share=0.9, available=True)
    )
    assert v.signal == "neutral"
    assert any("échantillon trop faible" in p for p in v.points)


def test_dominant_seller_is_concern():
    v = judge_sell_distribution(
        SellDistributionFacts(sellers_examined=20, total_sold_usd=10_000.0, top_seller_share=0.55, available=True)
    )
    assert v.signal == "concern"
    assert any("55%" in p for p in v.points)


def test_spread_selling_is_neutral():
    v = judge_sell_distribution(
        SellDistributionFacts(sellers_examined=100, total_sold_usd=10_000.0, top_seller_share=0.05, available=True)
    )
    assert v.signal == "neutral"
    assert any("étalée sur 100 vendeurs" in p for p in v.points)


def test_exactly_at_threshold_is_concern():
    v = judge_sell_distribution(
        SellDistributionFacts(sellers_examined=10, total_sold_usd=1_000.0, top_seller_share=0.40, available=True)
    )
    assert v.signal == "concern"


# ── Récolte on-chain (Dune factice) ──────────────────────────────────────────


class _Seller:
    def __init__(self, address, total_sold_usd):
        self.address, self.total_sold_usd = address, total_sold_usd


class _SellDistributionResult:
    def __init__(self, sellers, available=True, error=None):
        self.sellers, self.available, self.error = sellers, available, error


class _FakeDuneModule:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def get_token_sell_distribution(self, contract, *, blockchain="base", lookback_days=90):
        self.calls.append((contract, blockchain, lookback_days))
        return self._result


@pytest.mark.asyncio
async def test_gather_dune_unavailable_propagates():
    dune_module = _FakeDuneModule(_SellDistributionResult([], available=False, error="clé absente"))
    facts = await gather_sell_distribution_facts(TOKEN, dune_module=dune_module)
    assert facts.available is False
    assert facts.error == "clé absente"


@pytest.mark.asyncio
async def test_gather_no_sellers_is_examined_zero():
    dune_module = _FakeDuneModule(_SellDistributionResult([]))
    facts = await gather_sell_distribution_facts(TOKEN, dune_module=dune_module)
    assert facts.available is True
    assert facts.sellers_examined == 0
    assert facts.top_seller_share is None


@pytest.mark.asyncio
async def test_gather_computes_top_seller_share():
    sellers = [
        _Seller("0x" + "1" * 40, 400.0),
        _Seller("0x" + "2" * 40, 300.0),
        _Seller("0x" + "3" * 40, 300.0),
    ]
    dune_module = _FakeDuneModule(_SellDistributionResult(sellers))
    facts = await gather_sell_distribution_facts(TOKEN, dune_module=dune_module)
    assert facts.available is True
    assert facts.sellers_examined == 3
    assert facts.total_sold_usd == pytest.approx(1000.0)
    assert facts.top_seller_share == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_gather_ignores_zero_amount_sellers():
    sellers = [_Seller("0x" + "1" * 40, 100.0), _Seller("0x" + "2" * 40, 0.0)]
    dune_module = _FakeDuneModule(_SellDistributionResult(sellers))
    facts = await gather_sell_distribution_facts(TOKEN, dune_module=dune_module)
    assert facts.sellers_examined == 1


@pytest.mark.asyncio
async def test_gather_passes_lookback_days_through():
    dune_module = _FakeDuneModule(_SellDistributionResult([]))
    await gather_sell_distribution_facts(TOKEN, lookback_days=30, dune_module=dune_module)
    assert dune_module.calls == [(TOKEN, "base", 30)]
