import pytest

from app.models.schemas import PairSummary, TokenInfo
from app.services.dexscreener import DexScreenerClient


def test_parse_pair_full_fields():
    raw = {
        "chainId": "solana",
        "dexId": "raydium",
        "url": "https://dexscreener.com/solana/abc",
        "pairAddress": "abc123",
        "baseToken": {"address": "t1", "name": "Bonk", "symbol": "BONK"},
        "quoteToken": {"address": "t2", "name": "USD Coin", "symbol": "USDC"},
        "priceUsd": "0.0000123",
        "priceNative": "0.0000001",
        "priceChange": {"m5": 1.2, "h1": -2.3, "h6": 4.5, "h24": 12.0},
        "volume": {"m5": 1000, "h1": 5000, "h6": 20000, "h24": 80000},
        "liquidity": {"usd": 150000, "base": 100, "quote": 50000},
        "marketCap": 900000,
        "fdv": 1200000,
        "pairCreatedAt": 1700000000000,
        "labels": ["v2"],
        "txns": {
            "m5": {"buys": 10, "sells": 5},
            "h24": {"buys": 100, "sells": 80},
        },
        "boosts": {"active": 3},
        "info": {
            "imageUrl": "https://cdn.dexscreener.com/token.png",
            "websites": [{"url": "https://bonk.com"}],
            "socials": [{"platform": "twitter", "handle": "bonk"}],
        },
    }
    pair = DexScreenerClient()._parse_pair(raw)
    assert pair.chain_id == "solana"
    assert pair.price_usd == 0.0000123
    assert pair.volume_h24 == 80000
    assert pair.txns is not None
    assert pair.txns.h24 is not None
    assert pair.txns.h24.buys == 100
    assert pair.pair_created_at == 1700000000000
    assert pair.boosts_active == 3
    assert pair.websites == ["https://bonk.com"]


def _pair(pair_address: str, base_address: str, liquidity_usd: float, price_usd: float) -> PairSummary:
    return PairSummary(
        chain_id="base",
        dex_id="aerodrome",
        pair_address=pair_address,
        url="https://dexscreener.com",
        base_token=TokenInfo(address=base_address, name="TOK", symbol="TOK"),
        quote_token=TokenInfo(address="0xquote", name="QUOTE", symbol="QUOTE"),
        price_usd=price_usd,
        liquidity_usd=liquidity_usd,
    )


@pytest.mark.asyncio
async def test_resolve_token_to_best_pair_ignores_pair_where_token_is_only_quote(monkeypatch):
    """19/07 -- même correctif que côté aria-core (reproduction de l'incident réel
    PLAZM/ESHARE, position paper-trading) : /token-pairs/v1 renvoie TOUTE paire
    impliquant le token, y compris comme simple QUOTE du pool d'un AUTRE token de
    base -- la vitrine ne doit jamais afficher le prix d'un token différent de
    celui demandé, même si cette paire est la plus liquide du lot."""
    token_address = "0x" + "a" * 40
    other_token_as_base = _pair("other_pool", "0x" + "b" * 40, 999_999.0, 0.01759)
    own_pair = _pair("own_pool", token_address, 100.0, 5.84)

    client = DexScreenerClient()

    async def fake_get_token_pairs(chain_id, addr):
        return [other_token_as_base, own_pair]

    monkeypatch.setattr(client, "get_token_pairs", fake_get_token_pairs)
    result = await client.resolve_token_to_best_pair("base", token_address)

    assert result is not None
    assert result.pair_address == "own_pool"
    assert result.price_usd == 5.84


@pytest.mark.asyncio
async def test_resolve_token_to_best_pair_none_when_token_never_the_base(monkeypatch):
    token_address = "0x" + "a" * 40
    other_token_as_base = _pair("other_pool", "0x" + "b" * 40, 999_999.0, 0.01759)

    client = DexScreenerClient()

    async def fake_get_token_pairs(chain_id, addr):
        return [other_token_as_base]

    monkeypatch.setattr(client, "get_token_pairs", fake_get_token_pairs)
    result = await client.resolve_token_to_best_pair("base", token_address)

    assert result is None