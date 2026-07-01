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