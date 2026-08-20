"""Real-time pump.fun trade stream with buyer identity (20/08).

Payload bytes below are built with the SAME layout confirmed by decoding real
live events off mainnet before the module was written -- never a guessed shape.
No network in any test."""
from __future__ import annotations

import base64
import struct

import base58
import pytest

from aria_core.services import pumpfun_trade_stream as stream


def _pubkey(label: str) -> bytes:
    """Any label -> 32 deterministic bytes. Deliberately NOT base58-decoding
    the label: the on-chain field is raw bytes, so a test that required its
    fixtures to already be valid base58 was testing the fixture, not the
    module (and broke on characters base58 happens to exclude: 0, O, I, l)."""
    import hashlib

    return hashlib.sha256(label.encode()).digest()


def _event_bytes(*, mint="MintAAA", sol=0.05, is_buy=True, user="UserAAA", discriminator=None) -> bytes:
    buf = bytearray(stream.TRADE_EVENT_MIN_LEN)
    buf[0:8] = discriminator if discriminator is not None else stream.TRADE_EVENT_DISCRIMINATOR
    buf[stream.OFF_MINT:stream.OFF_MINT + 32] = _pubkey(mint)
    struct.pack_into("<Q", buf, stream.OFF_SOL_AMOUNT, int(sol * 1e9))
    buf[stream.OFF_IS_BUY] = 1 if is_buy else 0
    buf[stream.OFF_USER:stream.OFF_USER + 32] = _pubkey(user)
    return bytes(buf)


def _notif(*events, err=None) -> dict:
    logs = ["Program log: instruction: Buy"] + [
        "Program data: " + base64.b64encode(e).decode() for e in events
    ]
    return {"params": {"result": {"value": {"logs": logs, "err": err, "signature": "sig"}}}}


def _mint_of(raw: bytes) -> str:
    return base58.b58encode(raw[stream.OFF_MINT:stream.OFF_MINT + 32]).decode()


def test_a_real_shaped_event_decodes_to_mint_amount_side_and_user():
    raw = _event_bytes(sol=0.0045, is_buy=True)
    decoded = stream.decode_trade_event(raw)

    assert decoded is not None
    mint, sol_amount, is_buy, user = decoded
    assert sol_amount == pytest.approx(0.0045)
    assert is_buy is True
    assert len(user) > 0


def test_a_foreign_discriminator_is_not_parsed_as_a_trade():
    raw = _event_bytes(discriminator=bytes.fromhex("0011223344556677"))
    assert stream.decode_trade_event(raw) is None


def test_a_truncated_payload_is_skipped_rather_than_misparsed():
    """These bytes come off a public chain and are attacker-influenceable --
    a short buffer must never be read past its end."""
    assert stream.decode_trade_event(_event_bytes()[:20]) is None
    assert stream.decode_trade_event(b"") is None


def test_distinct_buyers_are_counted_once_each():
    """The whole point: trade velocity is forgeable by one wallet firing many
    buys, distinct buyers is not."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes(user="UserAAA")
    mint = _mint_of(raw)
    s.handle_notification(_notif(raw, raw, raw))  # same wallet, three buys

    flow = s.get_flow(mint)
    assert flow.buy_count == 3
    assert flow.distinct_buyers == 1


def test_several_wallets_buying_reads_as_real_traction():
    s = stream.PumpFunTradeStream()
    events = [_event_bytes(user=u) for u in ("UserAAA", "UserBBB", "UserCCC")]
    s.handle_notification(_notif(*events))

    flow = s.get_flow(_mint_of(events[0]))
    assert flow.distinct_buyers == 3
    assert flow.buy_count == 3


def test_buys_and_sells_are_tracked_separately():
    s = stream.PumpFunTradeStream()
    buy = _event_bytes(user="UserAAA", is_buy=True, sol=0.2)
    sell = _event_bytes(user="UserBBB", is_buy=False, sol=0.1)
    s.handle_notification(_notif(buy, sell))

    flow = s.get_flow(_mint_of(buy))
    assert (flow.distinct_buyers, flow.distinct_sellers) == (1, 1)
    assert flow.buy_sol_volume == pytest.approx(0.2)  # sell volume never inflates buy volume


def test_a_failed_transaction_is_never_counted_as_a_trade():
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    assert s.handle_notification(_notif(raw, err={"InstructionError": [0, "Custom"]})) == 0
    assert s.get_flow(_mint_of(raw)).buy_count == 0


def test_an_unseen_mint_reads_as_zero_buyers_not_as_missing():
    """Nobody having bought IS the answer -- the caller must not have to
    distinguish "no data" from "no buyers" here."""
    flow = stream.PumpFunTradeStream().get_flow("NeverSeen")
    assert flow.distinct_buyers == 0
    assert flow.seconds_active is None


def test_a_non_notification_message_is_ignored():
    s = stream.PumpFunTradeStream()
    assert s.handle_notification({"result": 123, "id": 1}) == 0
    assert s.handle_notification({}) == 0


def test_idle_mints_are_pruned_so_memory_cannot_grow_unbounded():
    """An always-on program-wide stream sees every mint pump.fun emits."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    s.handle_notification(_notif(raw))
    mint = _mint_of(raw)
    assert s.get_flow(mint).buy_count == 1

    import time as _t
    dropped = s._prune(now=_t.time() + stream.BUYER_SET_TTL_SECONDS + 1)

    assert dropped == 1
    assert s.get_flow(mint).distinct_buyers == 0


def test_seconds_active_reflects_the_real_trading_window():
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    s.handle_notification(_notif(raw))
    flow = s.get_flow(_mint_of(raw))
    assert flow.seconds_active is not None and flow.seconds_active >= 0.0


# --- 20/08, scam-shape metrics -------------------------------------------
# "Si on comprend les scam alors on comprend le jeu" (operator). Distinct
# buyers alone can be manufactured; these expose the two cheapest fakes.

def test_one_wallet_supplying_most_of_the_volume_is_visible():
    """Wash trading: many buys, many SOL, but it is all one actor."""
    s = stream.PumpFunTradeStream()
    whale = _event_bytes(user="UserAAA", sol=9.0)
    small = _event_bytes(user="UserBBB", sol=1.0)
    s.handle_notification(_notif(whale, small))

    flow = s.get_flow(_mint_of(whale))
    assert flow.distinct_buyers == 2  # looks like a crowd...
    assert flow.top_buyer_share == pytest.approx(0.9)  # ...but one wallet is 90% of it


def test_a_genuinely_spread_crowd_shows_a_low_top_buyer_share():
    s = stream.PumpFunTradeStream()
    events = [_event_bytes(user=u, sol=1.0) for u in ("UserAAA", "UserBBB", "UserCCC", "UserDDD")]
    s.handle_notification(_notif(*events))

    assert s.get_flow(_mint_of(events[0])).top_buyer_share == pytest.approx(0.25)


def test_top_buyer_share_is_none_when_nothing_was_bought():
    """Never a fabricated 0.0 -- no buys means the ratio is undefined."""
    s = stream.PumpFunTradeStream()
    sell = _event_bytes(user="UserAAA", is_buy=False)
    s.handle_notification(_notif(sell))

    assert s.get_flow(_mint_of(sell)).top_buyer_share is None


def test_sell_pressure_exposes_an_exit_already_under_way():
    s = stream.PumpFunTradeStream()
    buy = _event_bytes(user="UserAAA", is_buy=True)
    sells = [_event_bytes(user=u, is_buy=False) for u in ("UserBBB", "UserCCC", "UserDDD")]
    s.handle_notification(_notif(buy, *sells))

    flow = s.get_flow(_mint_of(buy))
    assert flow.sell_pressure == pytest.approx(3.0)  # 3 sellers leaving per buyer arriving


# --- 20/08, time-window derivatives --------------------------------------
# The lure-phase signal (are buyers still arriving?) and the ultra-early exit
# signal (is the exit accelerating?). Both fire BEFORE the price reacts --
# which is the whole point: on the operator's own reference chart the climb
# lasted 6 minutes and the dump was vertical, so a price-based stop cannot
# get out in time.

def _at(s, mint_raw, *, t, user, is_buy=True):
    """Injects one trade at an explicit timestamp."""
    st = s._state.setdefault(_mint_of(mint_raw), stream._MintState())
    st.recent.append((t, user, is_buy))
    (st.buyers if is_buy else st.sellers).add(user)


def test_a_crowd_still_arriving_reads_as_acceleration_above_one():
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    now = 1000.0
    for u in ("a1", "a2"):                       # prior window (20-40s ago)
        _at(s, raw, t=now - 30, user=u)
    for u in ("b1", "b2", "b3", "b4"):           # recent window (last 20s)
        _at(s, raw, t=now - 5, user=u)

    assert s.buyer_acceleration(_mint_of(raw), window=20.0, now=now) == pytest.approx(2.0)


def test_a_crowd_thinning_out_reads_below_one():
    """The lure phase ending -- visible before the price turns."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    now = 1000.0
    for u in ("a1", "a2", "a3", "a4"):
        _at(s, raw, t=now - 30, user=u)
    _at(s, raw, t=now - 5, user="b1")

    assert s.buyer_acceleration(_mint_of(raw), window=20.0, now=now) == pytest.approx(0.25)


def test_acceleration_is_none_when_there_is_nothing_to_compare():
    """A brand-new token with no prior window must not read as a ratio."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    now = 1000.0
    _at(s, raw, t=now - 2, user="b1")

    assert s.buyer_acceleration(_mint_of(raw), window=20.0, now=now) is None


def test_an_accelerating_exit_shows_a_positive_sell_pressure_slope():
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    now = 1000.0
    # prior window: 2 buyers, 1 seller  -> 0.5 sellers per buyer
    _at(s, raw, t=now - 30, user="a1"); _at(s, raw, t=now - 30, user="a2")
    _at(s, raw, t=now - 30, user="s1", is_buy=False)
    # recent window: 1 buyer, 3 sellers -> 3.0 sellers per buyer
    _at(s, raw, t=now - 5, user="b1")
    for u in ("s2", "s3", "s4"):
        _at(s, raw, t=now - 5, user=u, is_buy=False)

    slope = s.sell_pressure_slope(_mint_of(raw), window=20.0, now=now)
    assert slope == pytest.approx(2.5)  # 3.0 - 0.5, exit clearly accelerating


def test_sell_pressure_slope_is_none_rather_than_reading_as_calm():
    """An undefined ratio must never be reported as zero -- that would look
    like a healthy token when it is simply unmeasured."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    now = 1000.0
    for u in ("s1", "s2"):
        _at(s, raw, t=now - 5, user=u, is_buy=False)  # sellers only, no buyers

    assert s.sell_pressure_slope(_mint_of(raw), window=20.0, now=now) is None


def test_the_trade_log_stays_bounded_under_sustained_flow():
    """~50 trades/s across ~170 tokens live -- an unbounded log would be the
    process's biggest memory consumer within minutes."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    for i in range(400):
        s._record(_mint_of(raw), 0.01, True, f"user{i}")

    st = s._state[_mint_of(raw)]
    assert all(t >= st.recent[-1][0] - stream.TRADE_LOG_WINDOW_SECONDS for t, _, _ in st.recent)


# --- 20/08, candidate sourcing for the LATE-BONDING pocket ---------------
# The program-wide stream already sees every actively-traded token, so
# "which tokens are alive right now" is a local read -- no scanning loop, no
# second subscription.

def test_active_mints_ranks_by_distinct_buyers():
    s = stream.PumpFunTradeStream()
    hot = _event_bytes(mint="MintAAA")
    cold = _event_bytes(mint="MintBBB")
    s.handle_notification(_notif(*[_event_bytes(mint="MintAAA", user=f"user{i+1}") for i in range(5)]))
    s.handle_notification(_notif(_event_bytes(mint="MintBBB", user="userSingle")))

    ranked = s.active_mints(min_buyers=1)
    assert ranked[0] == _mint_of(hot)
    assert _mint_of(cold) in ranked


def test_a_token_bought_by_a_single_wallet_can_be_filtered_out():
    """One wallet must not be able to put a dead token on the candidate list."""
    s = stream.PumpFunTradeStream()
    raw = _event_bytes(mint="MintBBB")
    s.handle_notification(_notif(raw, raw, raw))  # 3 buys, 1 wallet

    assert s.active_mints(min_buyers=3) == []


def test_a_token_that_stopped_trading_drops_off_the_list():
    s = stream.PumpFunTradeStream()
    raw = _event_bytes()
    s.handle_notification(_notif(raw))
    import time as _t

    assert s.active_mints(now=_t.time() + 3600) == []
