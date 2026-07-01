import pytest

from app.models.schemas import PairSummary, TokenInfo
from app.services import pair_store


@pytest.fixture
async def pair_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(pair_store, "DB_PATH", str(db_file))
    await pair_store.init_pair_store()
    yield


def _sample_pair(symbol: str = "BONK", change: float = 10.0) -> PairSummary:
    return PairSummary(
        chain_id="solana",
        dex_id="raydium",
        pair_address=f"addr-{symbol}",
        url="https://dexscreener.com",
        base_token=TokenInfo(address="t1", name=symbol, symbol=symbol),
        quote_token=TokenInfo(address="t2", name="USDC", symbol="USDC"),
        price_usd=0.01,
        price_change_h24=change,
        volume_h24=50000,
        liquidity_usd=100000,
        pair_created_at=1700000000000,
    )


@pytest.mark.asyncio
async def test_upsert_and_rank(pair_db):
    await pair_store.upsert_pairs([_sample_pair("AAA", 50), _sample_pair("BBB", -20)], "seed")
    gainers = await pair_store.get_ranked_pairs(gainers=True, limit=5)
    assert gainers[0].base_token.symbol == "AAA"
    losers = await pair_store.get_ranked_pairs(gainers=False, limit=5)
    assert losers[0].base_token.symbol == "BBB"


@pytest.mark.asyncio
async def test_index_stats(pair_db):
    await pair_store.upsert_pairs([_sample_pair()], "trending")
    stats = await pair_store.get_index_stats()
    assert stats.total_pairs == 1
    assert stats.by_feed.get("trending") == 1