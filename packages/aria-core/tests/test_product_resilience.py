"""Signal 'résilience produit' -- l'usage on-chain réel du token tient-il
pendant que le marché recule ? (10/08, demande opérateur: "capter sa qualité
alors que le marché decline est un sacré atout")."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aria_core.skills.product_resilience import (
    ProductResilienceFacts,
    gather_product_resilience_facts,
    judge_product_resilience,
)

TOKEN = "0x" + "a" * 40


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_product_resilience(ProductResilienceFacts(available=False, error="Blockscout indisponible"))
    assert v.signal == "unknown"
    assert "Blockscout indisponible" in v.points[0]


def test_too_few_prior_transfers_is_unknown():
    v = judge_product_resilience(
        ProductResilienceFacts(
            recent_transfer_count=5, prior_transfer_count=3, market_drawdown_pct=-20.0, available=True,
        )
    )
    assert v.signal == "unknown"
    assert any("échantillon trop faible" in p for p in v.points)


def test_missing_market_drawdown_is_unknown():
    v = judge_product_resilience(
        ProductResilienceFacts(
            recent_transfer_count=50, prior_transfer_count=40, market_drawdown_pct=None, available=True,
        )
    )
    assert v.signal == "unknown"


def test_market_declining_and_activity_holding_is_resilient():
    v = judge_product_resilience(
        ProductResilienceFacts(
            recent_transfer_count=45,
            prior_transfer_count=50,
            recent_unique_addresses=30,
            market_drawdown_pct=-22.0,
            available=True,
        )
    )
    assert v.signal == "resilient"
    assert any("RARE" in p for p in v.points)


def test_market_declining_and_activity_collapsing_is_declining():
    v = judge_product_resilience(
        ProductResilienceFacts(
            recent_transfer_count=10,
            prior_transfer_count=50,
            market_drawdown_pct=-22.0,
            available=True,
        )
    )
    assert v.signal == "declining"


def test_market_not_declining_is_neutral_regardless_of_activity():
    v = judge_product_resilience(
        ProductResilienceFacts(
            recent_transfer_count=5,
            prior_transfer_count=50,
            market_drawdown_pct=-2.0,
            available=True,
        )
    )
    assert v.signal == "neutral"


# ── Collecte (Blockscout + marché mockés) ───────────────────────────────────


@dataclass
class _FakeTransfer:
    from_address: str
    to_address: str
    timestamp: str | None
    tx_hash: str = "0xdead"
    token_address: str | None = None
    token_symbol: str | None = None
    token_name: str | None = None
    amount: float | None = None
    method: str | None = None
    error: str | None = None


@dataclass
class _FakeTransfersResult:
    transfers: list = field(default_factory=list)
    available: bool = True
    error: str | None = None


class _FakeBlockscoutClient:
    def __init__(self, result):
        self._result = result
        self.calls: list[str] = []

    async def get_token_transfers_for_token(self, token_address, limit=500, *, max_pages=10):
        self.calls.append(token_address)
        return self._result


@dataclass
class _FakeSentimentReading:
    drawdown_from_high_pct: float | None


class _FakeMarketSentimentModule:
    def __init__(self, closes, drawdown):
        self._closes = closes
        self._drawdown = drawdown
        self.calls: list[tuple] = []

    async def _fetch_recent_closes(self, coin_id, *, client=None, days=14):
        self.calls.append((coin_id, days))
        return self._closes

    def classify_sentiment(self, closes, *, pair=""):
        return _FakeSentimentReading(drawdown_from_high_pct=self._drawdown)


def _iso(days_ago: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_gather_blockscout_unavailable_propagates():
    client = _FakeBlockscoutClient(_FakeTransfersResult([], available=False, error="rate limited"))
    market = _FakeMarketSentimentModule([1.0], -10.0)
    facts = await gather_product_resilience_facts(TOKEN, blockscout_client=client, market_sentiment_module=market)
    assert facts.available is False
    assert facts.error == "rate limited"


@pytest.mark.asyncio
async def test_gather_no_transfers_is_unavailable():
    client = _FakeBlockscoutClient(_FakeTransfersResult([]))
    market = _FakeMarketSentimentModule([1.0], -10.0)
    facts = await gather_product_resilience_facts(TOKEN, blockscout_client=client, market_sentiment_module=market)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_splits_recent_vs_prior_window_correctly():
    transfers = [
        _FakeTransfer("0x1", "0x2", _iso(2)),  # recent (<7d)
        _FakeTransfer("0x2", "0x3", _iso(5)),  # recent (<7d)
        _FakeTransfer("0x3", "0x4", _iso(10)),  # prior (7-14d)
        _FakeTransfer("0x4", "0x5", _iso(13)),  # prior (7-14d)
        _FakeTransfer("0x5", "0x6", _iso(20)),  # too old, ignored
    ]
    client = _FakeBlockscoutClient(_FakeTransfersResult(transfers))
    market = _FakeMarketSentimentModule([1.0], -18.0)
    facts = await gather_product_resilience_facts(TOKEN, blockscout_client=client, market_sentiment_module=market)

    assert facts.available is True
    assert facts.recent_transfer_count == 2
    assert facts.prior_transfer_count == 2
    assert facts.recent_unique_addresses == 3  # {0x1, 0x2, 0x3} from the two recent transfers
    assert facts.market_drawdown_pct == -18.0
    assert client.calls == [TOKEN]
    assert market.calls == [("bitcoin", 14)]


@pytest.mark.asyncio
async def test_gather_ignores_transfers_with_no_timestamp():
    transfers = [
        _FakeTransfer("0x1", "0x2", None),
        _FakeTransfer("0x2", "0x3", _iso(1)),
    ]
    client = _FakeBlockscoutClient(_FakeTransfersResult(transfers))
    market = _FakeMarketSentimentModule([1.0], -18.0)
    facts = await gather_product_resilience_facts(TOKEN, blockscout_client=client, market_sentiment_module=market)
    assert facts.recent_transfer_count == 1


@pytest.mark.asyncio
async def test_gather_never_blocks_on_blockscout_exception():
    class _RaisingClient:
        async def get_token_transfers_for_token(self, *args, **kwargs):
            raise RuntimeError("boom")

    facts = await gather_product_resilience_facts(TOKEN, blockscout_client=_RaisingClient())
    assert facts.available is False
    assert "boom" in (facts.error or "")


@pytest.mark.asyncio
async def test_gather_market_context_optional_on_sentiment_failure():
    class _RaisingMarketModule:
        async def _fetch_recent_closes(self, *args, **kwargs):
            raise RuntimeError("coingecko down")

    transfers = [_FakeTransfer("0x1", "0x2", _iso(1))]
    client = _FakeBlockscoutClient(_FakeTransfersResult(transfers))
    facts = await gather_product_resilience_facts(
        TOKEN, blockscout_client=client, market_sentiment_module=_RaisingMarketModule(),
    )
    assert facts.available is True
    assert facts.market_drawdown_pct is None
