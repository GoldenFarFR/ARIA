

# --- 20/08, trade-flow counters ------------------------------------------
# Every accountNotification on a bonding curve IS a trade (the account only
# changes when someone buys or sells), and the quote reserve's direction says
# which. This makes the "is anyone actually buying this" signal readable
# DURING the pre-entry window, from a subscription that already exists --
# PumpPortal's own subscribeTokenTrade is metered (0.01 SOL/10k events) and
# returned zero events on 18 real fresh tokens in a live 30s test.

def _notif(pool_sub_id: int, account_b64: str) -> dict:
    return {"method": "accountNotification",
            "params": {"subscription": pool_sub_id, "result": {"value": {"data": [account_b64, "base64"]}}}}


def _curve_b64(*, quote_reserves: int) -> str:
    import base64, struct
    from aria_core.services import pumpfun_bonding_ws as m
    buf = bytearray(m.BONDING_CURVE_ACCOUNT_MIN_LEN)
    buf[0:8] = m.BONDING_CURVE_DISCRIMINATOR
    struct.pack_into("<Q", buf, m.OFF_VIRTUAL_TOKEN_RESERVES, 1_000_000_000_000)
    struct.pack_into("<Q", buf, m.OFF_VIRTUAL_QUOTE_RESERVES, quote_reserves)
    struct.pack_into("<Q", buf, m.OFF_REAL_QUOTE_RESERVES, quote_reserves)
    struct.pack_into("<Q", buf, m.OFF_TOKEN_TOTAL_SUPPLY, 1_000_000_000_000)
    buf[m.OFF_COMPLETE] = 0
    return base64.b64encode(bytes(buf)).decode()


def _feed_with(pool="poolA"):
    from aria_core.services.pumpfun_bonding_ws import PumpFunBondingWebSocketFeed
    return PumpFunBondingWebSocketFeed(), {1: pool}


def test_a_rising_quote_reserve_counts_as_a_buy():
    feed, ids = _feed_with()
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=30_000_000_000)), ids)
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=31_000_000_000)), ids)

    assert feed._buys_since_read.get("poolA") == 1
    assert feed._sells_since_read.get("poolA", 0) == 0


def test_a_falling_quote_reserve_counts_as_a_sell():
    feed, ids = _feed_with()
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=30_000_000_000)), ids)
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=29_000_000_000)), ids)

    assert feed._sells_since_read.get("poolA") == 1
    assert feed._buys_since_read.get("poolA", 0) == 0


def test_the_very_first_notification_is_not_counted_as_a_direction():
    """No previous state means no delta -- inventing a direction there would
    fabricate a buy on every newly subscribed pool."""
    feed, ids = _feed_with()
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=30_000_000_000)), ids)

    assert feed._buys_since_read.get("poolA", 0) == 0
    assert feed._sells_since_read.get("poolA", 0) == 0
    assert feed._trades_total.get("poolA") == 1  # still counted as activity


def test_counters_are_dropped_when_a_pool_is_unsubscribed():
    """Per-pool state must never leak -- the same leak class once exceeded the
    RPC's accountSubscribe ceiling."""
    feed, ids = _feed_with()
    feed._apply_notification(_notif(1, _curve_b64(quote_reserves=30_000_000_000)), ids)
    feed.remove_pools(["poolA"])

    assert "poolA" not in feed._trades_total
    assert "poolA" not in feed._first_seen_at
