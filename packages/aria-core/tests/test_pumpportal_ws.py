"""services/pumpportal_ws.py -- pure message parsing and the live feed's
queue/reconnect/backpressure behavior. Never a real network call --
websocket connections go through an injected fake, mirroring
test_pumpswap_ws.py's own FakeWebSocket pattern exactly."""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_core.services import pumpportal_ws

# --- parse_new_token_message: pure, no I/O ----------------------------------

REAL_CREATE_MESSAGE = {
    # Verbatim shape captured live 19/08 from a real 45s connection to
    # wss://pumpportal.fun/api/data (see module docstring) -- never guessed.
    "signature": "Yomvo4JmS2tQL9g8xxsqGLfxY2KMMuTguzQ1PbwMWoqSSz3AqpkyqMi243XbhdifVcjsb6CUA5k8ZumZH6AWos8",
    "mint": "DPiCQ3RKk9Z4NrabKGFA4DmMEz7RKbNYTQupCQqBNzZy",
    "traderPublicKey": "bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa",
    "txType": "create",
    "initialBuy": 303858407.045613,
    "solAmount": 11.85185185,
    "bondingCurveKey": "ErUB84BnC1pHoxQbjYZKFvTHbWz1uAAqJFxwKyr2Pq2f",
    "vTokensInBondingCurve": 769141592.954387,
    "vSolInBondingCurve": 41.85185184999999,
    "marketCapSol": 54.41371554129692,
    "name": "quoll",
    "symbol": "quoll",
    "uri": "https://md.sdfgsdfsdf.uk/metadata/k9XFKnBX",
    "is_mayhem_mode": False,
    "pool": "pump",
}


def test_parse_new_token_message_extracts_real_fields():
    event = pumpportal_ws.parse_new_token_message(REAL_CREATE_MESSAGE)
    assert event is not None
    assert event.mint == "DPiCQ3RKk9Z4NrabKGFA4DmMEz7RKbNYTQupCQqBNzZy"
    assert event.symbol == "quoll"
    assert event.name == "quoll"
    assert event.pool == "pump"
    assert event.bonding_curve_key == "ErUB84BnC1pHoxQbjYZKFvTHbWz1uAAqJFxwKyr2Pq2f"
    assert event.market_cap_sol == pytest.approx(54.41371554129692)
    assert event.v_sol_in_bonding_curve == pytest.approx(41.85185184999999)
    assert event.v_tokens_in_bonding_curve == pytest.approx(769141592.954387)
    assert event.sol_amount == pytest.approx(11.85185185)
    assert event.initial_buy == pytest.approx(303858407.045613)
    assert event.signature == REAL_CREATE_MESSAGE["signature"]
    assert isinstance(event.detected_at, float) and event.detected_at > 0


def test_parse_new_token_message_rejects_subscribe_ack():
    assert pumpportal_ws.parse_new_token_message({"message": "Successfully subscribed to token creation events."}) is None


def test_parse_new_token_message_rejects_non_create_tx_type():
    msg = dict(REAL_CREATE_MESSAGE, txType="buy")
    assert pumpportal_ws.parse_new_token_message(msg) is None


def test_parse_new_token_message_rejects_missing_mint():
    msg = dict(REAL_CREATE_MESSAGE)
    del msg["mint"]
    assert pumpportal_ws.parse_new_token_message(msg) is None


def test_parse_new_token_message_rejects_non_dict():
    assert pumpportal_ws.parse_new_token_message("not a dict") is None
    assert pumpportal_ws.parse_new_token_message(None) is None


def test_parse_new_token_message_degrades_missing_optional_fields_independently():
    msg = {"txType": "create", "mint": "someMint111"}
    event = pumpportal_ws.parse_new_token_message(msg)
    assert event is not None
    assert event.mint == "someMint111"
    assert event.symbol is None
    assert event.bonding_curve_key is None
    assert event.market_cap_sol is None


# --- PumpPortalNewTokenFeed: queue/reconnect/backpressure, fake websocket ---

class FakeWebSocket:
    """Mimics the subset of the `websockets` connection interface this
    module uses. `to_push` is a list of raw JSON strings served in order on
    successive `recv()` calls; once exhausted, `recv()` blocks until the
    test cancels the feed (mirrors a real idle connection)."""

    def __init__(self, to_push: list[str] | None = None, *, fail_on_enter: bool = False):
        self.to_push = list(to_push or [])
        self.fail_on_enter = fail_on_enter
        self.sent: list[dict] = []
        self._idle = asyncio.Event()

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))

    async def recv(self) -> str:
        if self.to_push:
            return self.to_push.pop(0)
        await self._idle.wait()  # never resolves in a test -- recv() times out instead
        raise AssertionError("unreachable")

    async def __aenter__(self):
        if self.fail_on_enter:
            raise ConnectionRefusedError("simulated connect failure")
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_feed_subscribes_and_delivers_a_real_event(monkeypatch):
    monkeypatch.setattr(pumpportal_ws, "_RECV_POLL_TIMEOUT_SECONDS", 0.05)
    ws = FakeWebSocket(to_push=[
        json.dumps({"message": "Successfully subscribed to token creation events."}),
        json.dumps(REAL_CREATE_MESSAGE),
    ])
    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=lambda url: ws)
    await feed.start()
    try:
        event = await feed.next_event(timeout=2.0)
        assert event is not None
        assert event.mint == REAL_CREATE_MESSAGE["mint"]
        # Subscribe request sent exactly once, correct method name.
        assert ws.sent == [{"method": "subscribeNewToken"}]
        # The ack message itself never surfaces as an event.
        assert feed.pending_count() == 0
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_feed_next_event_times_out_without_raising(monkeypatch):
    monkeypatch.setattr(pumpportal_ws, "_RECV_POLL_TIMEOUT_SECONDS", 0.05)
    ws = FakeWebSocket()
    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=lambda url: ws)
    await feed.start()
    try:
        event = await feed.next_event(timeout=0.2)
        assert event is None
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_feed_drops_and_counts_when_queue_full(monkeypatch):
    monkeypatch.setattr(pumpportal_ws, "_RECV_POLL_TIMEOUT_SECONDS", 0.05)
    second = dict(REAL_CREATE_MESSAGE, mint="secondMint222")
    ws = FakeWebSocket(to_push=[json.dumps(REAL_CREATE_MESSAGE), json.dumps(second)])
    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=lambda url: ws, queue_maxsize=1)
    await feed.start()
    try:
        first_event = await feed.next_event(timeout=2.0)
        assert first_event is not None
        # Give the read loop a moment to also attempt (and drop) the second
        # message before the test drains the queue again.
        for _ in range(50):
            if feed.dropped_count > 0:
                break
            await asyncio.sleep(0.01)
        # Either the second event made it in before the first was drained
        # (queue never actually full) or it was dropped -- both are valid
        # given real scheduling, but at most one of these two outcomes
        # should hold: never both an accepted second event AND a drop.
        second_event = await feed.next_event(timeout=0.2)
        if second_event is not None:
            assert feed.dropped_count == 0
        else:
            assert feed.dropped_count >= 1
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_feed_reconnects_after_a_connection_error(monkeypatch):
    monkeypatch.setattr(pumpportal_ws, "_RECV_POLL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(pumpportal_ws, "_RECONNECT_BACKOFF_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(pumpportal_ws, "_RECONNECT_BACKOFF_MAX_SECONDS", 0.02)

    attempts: list[FakeWebSocket] = []

    def connect_fn(url):
        ws = FakeWebSocket(to_push=[json.dumps(REAL_CREATE_MESSAGE)], fail_on_enter=(len(attempts) == 0))
        attempts.append(ws)
        return ws

    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=connect_fn)
    await feed.start()
    try:
        # First connection attempt fails on __aenter__ -- the feed must
        # reconnect (never crash/hang) and the SECOND attempt's real event
        # must still be delivered.
        event = await feed.next_event(timeout=2.0)
        assert event is not None
        assert len(attempts) == 2
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_feed_stop_is_clean_and_idempotent():
    ws = FakeWebSocket()
    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=lambda url: ws)
    await feed.start()
    await feed.stop()
    await feed.stop()  # must not raise on a second stop


@pytest.mark.asyncio
async def test_feed_only_ever_holds_one_connection_at_a_time(monkeypatch):
    """PumpPortal's own documented constraint (see module docstring): never
    open multiple simultaneous websocket connections. Structurally
    guaranteed here (one `_run_loop` task, one `async with self._connect()`
    block active at a time) -- this test asserts no two connect_fn calls
    are ever concurrently open."""
    monkeypatch.setattr(pumpportal_ws, "_RECV_POLL_TIMEOUT_SECONDS", 0.05)
    open_count = 0
    max_concurrent_open = 0

    class TrackedWS(FakeWebSocket):
        async def __aenter__(self):
            nonlocal open_count, max_concurrent_open
            open_count += 1
            max_concurrent_open = max(max_concurrent_open, open_count)
            return await super().__aenter__()

        async def __aexit__(self, *exc):
            nonlocal open_count
            open_count -= 1
            return await super().__aexit__(*exc)

    def connect_fn(url):
        return TrackedWS(to_push=[json.dumps(REAL_CREATE_MESSAGE)])

    feed = pumpportal_ws.PumpPortalNewTokenFeed(connect_fn=connect_fn)
    await feed.start()
    try:
        await feed.next_event(timeout=2.0)
        assert max_concurrent_open <= 1
    finally:
        await feed.stop()
