"""PumpFun bonding-curve feed (19/08) -- trade-flow counters and curve
progress. Isolated, no network: every account payload is built locally with
the SAME offsets the module decodes, taken from the official Anchor IDL."""
from __future__ import annotations

import pytest

from aria_core.services.pumpfun_bonding_ws import (
    NATIVE_SOL_QUOTE_MARKER,
    PumpFunBondingWebSocketFeed,
)


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


# --- 20/08, bonding-curve progress ---------------------------------------
# Operator's question: does entering near the END of the bonding curve work?
# Winrate already doubles from the <30% band (9.9%, n=1277) to 30-50% (20.9%,
# n=239), but ARIA has only 4 closures past 50% -- the sourcing never goes
# there. This exposes the axis so the band can finally be measured.

def test_a_fresh_curve_reads_near_zero_progress():
    from aria_core.services.pumpfun_bonding_ws import bonding_progress, INITIAL_CURVE_TOKENS
    decoded = {"real_token_reserves": INITIAL_CURVE_TOKENS * 10**6, "complete": False}
    assert bonding_progress(decoded) == pytest.approx(0.0)


def test_a_half_sold_curve_reads_near_half():
    from aria_core.services.pumpfun_bonding_ws import bonding_progress, INITIAL_CURVE_TOKENS
    decoded = {"real_token_reserves": INITIAL_CURVE_TOKENS * 10**6 // 2, "complete": False}
    assert bonding_progress(decoded) == pytest.approx(0.5, abs=1e-3)


def test_a_completed_curve_is_one_whatever_the_reserves_say():
    """`complete` is the authoritative fact -- reserves can lag it."""
    from aria_core.services.pumpfun_bonding_ws import bonding_progress
    assert bonding_progress({"real_token_reserves": 12345, "complete": True}) == 1.0


def test_reserves_inconsistent_with_the_provisional_constant_return_none():
    """INITIAL_CURVE_TOKENS is not read from chain -- anything it cannot
    explain must say None, never a fabricated ratio."""
    from aria_core.services.pumpfun_bonding_ws import bonding_progress, INITIAL_CURVE_TOKENS
    assert bonding_progress({"real_token_reserves": INITIAL_CURVE_TOKENS * 10**9, "complete": False}) is None
    assert bonding_progress({"real_token_reserves": -1, "complete": False}) is None
    assert bonding_progress({"complete": False}) is None
    assert bonding_progress(None) is None


# --- 20/08, bonding-curve PDA derivation ---------------------------------
# The LATE-BONDING pocket sources from the program-wide trade stream, which
# only knows MINTS. Every earlier caller got the curve address handed to it by
# PumpPortal's creation event, so nothing had needed to derive it -- and the
# pocket rejected every candidate until this existed.

def test_a_real_mint_derives_a_stable_curve_address():
    from aria_core.services.pumpfun_bonding_ws import derive_bonding_curve_address
    mint = "FceDnS4HVKb9EMnpvGrP3XrhAyyXSMakguMLxNGtp4oC"  # a real one from our own closures
    addr = derive_bonding_curve_address(mint)

    assert addr and addr != mint
    assert derive_bonding_curve_address(mint) == addr  # deterministic


def test_an_unparseable_mint_returns_none_never_a_fabricated_address():
    from aria_core.services.pumpfun_bonding_ws import derive_bonding_curve_address
    assert derive_bonding_curve_address("not-a-pubkey") is None
    assert derive_bonding_curve_address("") is None


def test_the_decoded_account_exposes_its_creator():
    """21/08 -- regression guard on a silent guardrail failure: callers read
    `account.creator`, the attribute did not exist, so `is_factory()` received
    None on every candidate and the token-factory filter never fired once."""
    from aria_core.services.pumpfun_bonding_ws import PumpFunBondingCurveAccount

    acct = PumpFunBondingCurveAccount(
        pool_address="pool", mint="mint", quote_mint="q", token_decimals=6,
        curve={"creator": "CreatorWallet111"},
    )
    assert acct.creator == "CreatorWallet111"
    assert getattr(acct, "creator", None) == "CreatorWallet111"


def test_a_curve_without_a_creator_reports_none_not_a_crash():
    from aria_core.services.pumpfun_bonding_ws import PumpFunBondingCurveAccount

    acct = PumpFunBondingCurveAccount(
        pool_address="pool", mint="mint", quote_mint="q", token_decimals=6,
    )
    assert acct.creator is None
