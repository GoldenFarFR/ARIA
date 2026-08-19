"""services/pumpswap_ws.py -- decode logic (pure, offset-verified), account
self-verification (resolve_pool_accounts), and the live feed's in-memory
snapshot/staleness/reconnect logic. Never a real network call -- httpx
requests go through httpx.MockTransport, websocket connections go through an
injected fake. Same rigor as every other service test in this dome."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct

import httpx
import pytest

from aria_core.services import pumpswap_ws
from aria_core.services.coingecko import SimplePriceResult


def _pk_bytes(label: str) -> bytes:
    """Deterministic 32-byte fake pubkey, unique per label."""
    return hashlib.sha256(label.encode()).digest()


def _pk(label: str) -> str:
    return pumpswap_ws._pubkey_from_bytes(_pk_bytes(label))


def _build_pool_raw(*, base_mint: bytes, quote_mint: bytes, base_ta: bytes, quote_ta: bytes) -> bytes:
    buf = bytearray(pumpswap_ws.OFF_POOL_QUOTE_TOKEN_ACCOUNT + 32)
    buf[0:8] = pumpswap_ws.PUMPSWAP_POOL_DISCRIMINATOR
    buf[pumpswap_ws.OFF_POOL_BASE_MINT:pumpswap_ws.OFF_POOL_BASE_MINT + 32] = base_mint
    buf[pumpswap_ws.OFF_POOL_QUOTE_MINT:pumpswap_ws.OFF_POOL_QUOTE_MINT + 32] = quote_mint
    buf[pumpswap_ws.OFF_POOL_BASE_TOKEN_ACCOUNT:pumpswap_ws.OFF_POOL_BASE_TOKEN_ACCOUNT + 32] = base_ta
    buf[pumpswap_ws.OFF_POOL_QUOTE_TOKEN_ACCOUNT:pumpswap_ws.OFF_POOL_QUOTE_TOKEN_ACCOUNT + 32] = quote_ta
    return bytes(buf)


def _build_token_account_raw(*, mint: bytes, amount: int) -> bytes:
    buf = bytearray(165)
    buf[0:32] = mint
    struct.pack_into("<Q", buf, 64, amount)
    return bytes(buf)


def _build_mint_raw(decimals: int) -> bytes:
    buf = bytearray(82)
    buf[44] = decimals
    return bytes(buf)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# --- decode functions: pure, offset-based ------------------------------------

def test_decode_pool_account_extracts_the_4_pubkeys():
    base_mint, quote_mint = _pk_bytes("base"), _pk_bytes("quote")
    base_ta, quote_ta = _pk_bytes("base_ta"), _pk_bytes("quote_ta")
    raw = _build_pool_raw(base_mint=base_mint, quote_mint=quote_mint, base_ta=base_ta, quote_ta=quote_ta)
    decoded = pumpswap_ws.decode_pool_account(raw)
    assert decoded is not None
    assert decoded["base_mint"] == pumpswap_ws._pubkey_from_bytes(base_mint)
    assert decoded["quote_mint"] == pumpswap_ws._pubkey_from_bytes(quote_mint)
    assert decoded["pool_base_token_account"] == pumpswap_ws._pubkey_from_bytes(base_ta)
    assert decoded["pool_quote_token_account"] == pumpswap_ws._pubkey_from_bytes(quote_ta)


def test_decode_pool_account_rejects_wrong_discriminator():
    raw = bytearray(pumpswap_ws.OFF_POOL_QUOTE_TOKEN_ACCOUNT + 32)
    raw[0:8] = bytes(range(8))  # not the real PumpSwap discriminator
    assert pumpswap_ws.decode_pool_account(bytes(raw)) is None


def test_decode_pool_account_rejects_short_account():
    assert pumpswap_ws.decode_pool_account(pumpswap_ws.PUMPSWAP_POOL_DISCRIMINATOR + b"\x00" * 10) is None


def test_decode_token_account_extracts_mint_and_amount():
    mint = _pk_bytes("mint")
    raw = _build_token_account_raw(mint=mint, amount=123_456_789)
    decoded = pumpswap_ws.decode_token_account(raw)
    assert decoded == (pumpswap_ws._pubkey_from_bytes(mint), 123_456_789)


def test_decode_token_account_rejects_short_account():
    assert pumpswap_ws.decode_token_account(b"\x00" * 10) is None


def test_decode_mint_decimals():
    assert pumpswap_ws.decode_mint_decimals(_build_mint_raw(9)) == 9
    assert pumpswap_ws.decode_mint_decimals(_build_mint_raw(6)) == 6


def test_decode_mint_decimals_rejects_short_account():
    assert pumpswap_ws.decode_mint_decimals(b"\x00" * 10) is None


# --- resolve_pool_accounts: self-verification against real JSON-RPC shape ---

def _mock_transport(accounts_by_pubkey: dict[str, bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        pubkeys = payload["params"][0]
        value = []
        for pk in pubkeys:
            raw = accounts_by_pubkey.get(pk)
            value.append({"data": [_b64(raw), "base64"]} if raw is not None else None)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": value}})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_setup_gap(monkeypatch):
    monkeypatch.setattr(pumpswap_ws, "SETUP_REQUEST_GAP_SECONDS", 0.0)


@pytest.mark.asyncio
async def test_resolve_pool_accounts_fully_verified_pool():
    base_mint, quote_mint = _pk_bytes("base"), _pk_bytes("quote")
    base_ta, quote_ta = _pk_bytes("base_ta"), _pk_bytes("quote_ta")
    pool_addr = "poolAAA"
    accounts = {
        pool_addr: _build_pool_raw(base_mint=base_mint, quote_mint=quote_mint, base_ta=base_ta, quote_ta=quote_ta),
        pumpswap_ws._pubkey_from_bytes(base_ta): _build_token_account_raw(mint=base_mint, amount=1000),
        pumpswap_ws._pubkey_from_bytes(quote_ta): _build_token_account_raw(mint=quote_mint, amount=2000),
        pumpswap_ws._pubkey_from_bytes(base_mint): _build_mint_raw(6),
        pumpswap_ws._pubkey_from_bytes(quote_mint): _build_mint_raw(9),
    }
    async with httpx.AsyncClient(transport=_mock_transport(accounts)) as http_client:
        result = await pumpswap_ws.resolve_pool_accounts(http_client, [pool_addr])
    assert pool_addr in result
    resolved = result[pool_addr]
    assert resolved.base_decimals == 6
    assert resolved.quote_decimals == 9
    assert resolved.base_mint == pumpswap_ws._pubkey_from_bytes(base_mint)


@pytest.mark.asyncio
async def test_resolve_pool_accounts_excludes_pool_on_mint_mismatch():
    base_mint, quote_mint = _pk_bytes("base2"), _pk_bytes("quote2")
    base_ta, quote_ta = _pk_bytes("base_ta2"), _pk_bytes("quote_ta2")
    wrong_mint = _pk_bytes("not_the_expected_mint")
    pool_addr = "poolBBB"
    accounts = {
        pool_addr: _build_pool_raw(base_mint=base_mint, quote_mint=quote_mint, base_ta=base_ta, quote_ta=quote_ta),
        # base token account reports the WRONG mint -- offsets/self-verification must reject this pool.
        pumpswap_ws._pubkey_from_bytes(base_ta): _build_token_account_raw(mint=wrong_mint, amount=1000),
        pumpswap_ws._pubkey_from_bytes(quote_ta): _build_token_account_raw(mint=quote_mint, amount=2000),
        pumpswap_ws._pubkey_from_bytes(base_mint): _build_mint_raw(6),
        pumpswap_ws._pubkey_from_bytes(quote_mint): _build_mint_raw(9),
    }
    async with httpx.AsyncClient(transport=_mock_transport(accounts)) as http_client:
        result = await pumpswap_ws.resolve_pool_accounts(http_client, [pool_addr])
    assert result == {}


@pytest.mark.asyncio
async def test_resolve_pool_accounts_excludes_pool_on_missing_decimals():
    base_mint, quote_mint = _pk_bytes("base3"), _pk_bytes("quote3")
    base_ta, quote_ta = _pk_bytes("base_ta3"), _pk_bytes("quote_ta3")
    pool_addr = "poolCCC"
    accounts = {
        pool_addr: _build_pool_raw(base_mint=base_mint, quote_mint=quote_mint, base_ta=base_ta, quote_ta=quote_ta),
        pumpswap_ws._pubkey_from_bytes(base_ta): _build_token_account_raw(mint=base_mint, amount=1000),
        pumpswap_ws._pubkey_from_bytes(quote_ta): _build_token_account_raw(mint=quote_mint, amount=2000),
        pumpswap_ws._pubkey_from_bytes(base_mint): _build_mint_raw(6),
        # quote mint account missing entirely -> decimals unresolved -> pool excluded.
    }
    async with httpx.AsyncClient(transport=_mock_transport(accounts)) as http_client:
        result = await pumpswap_ws.resolve_pool_accounts(http_client, [pool_addr])
    assert result == {}


@pytest.mark.asyncio
async def test_resolve_pool_accounts_empty_input_returns_empty():
    async with httpx.AsyncClient(transport=_mock_transport({})) as http_client:
        result = await pumpswap_ws.resolve_pool_accounts(http_client, [])
    assert result == {}


# --- PumpSwapWebSocketFeed.get_snapshot: pure state, no network -------------

def _accounts(pool_addr="poolA", quote_mint=pumpswap_ws.WSOL_MINT) -> pumpswap_ws.PumpSwapPoolAccounts:
    return pumpswap_ws.PumpSwapPoolAccounts(
        pool_address=pool_addr, base_mint=_pk("base"), quote_mint=quote_mint,
        pool_base_token_account=_pk("base_ta"), pool_quote_token_account=_pk("quote_ta"),
        base_decimals=6, quote_decimals=9,
    )


def test_get_snapshot_unavailable_for_untracked_pool():
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    snap = feed.get_snapshot("nope")
    assert snap.available is False
    assert snap.error == "pool_not_tracked"


def test_get_snapshot_unavailable_before_first_notification():
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    feed._pools["poolA"] = _accounts()
    snap = feed.get_snapshot("poolA")
    assert snap.available is False
    assert snap.error == "no_notification_yet"


def test_get_snapshot_stale_past_max_staleness():
    feed = pumpswap_ws.PumpSwapWebSocketFeed(max_staleness_seconds=5.0)
    feed._pools["poolA"] = _accounts()
    feed._sol_usd = 150.0
    feed._updated_at["poolA"] = asyncio.get_event_loop().time() - 999999  # ancient (wall-clock-independent enough)
    import time
    feed._updated_at["poolA"] = time.time() - 999.0
    snap = feed.get_snapshot("poolA")
    assert snap.available is False
    assert snap.error == "stale"


def test_get_snapshot_unsupported_non_wsol_quote():
    import time
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    feed._pools["poolA"] = _accounts(quote_mint=_pk("some_token_2022_mint"))
    feed._sol_usd = 150.0
    feed._updated_at["poolA"] = time.time()
    snap = feed.get_snapshot("poolA")
    assert snap.available is False
    assert snap.error == "non_wsol_quote_unsupported"


def test_get_snapshot_unavailable_without_calibration():
    import time
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    feed._pools["poolA"] = _accounts()
    feed._updated_at["poolA"] = time.time()
    snap = feed.get_snapshot("poolA")
    assert snap.available is False
    assert snap.error == "no_sol_usd_calibration"


def test_get_snapshot_computes_price_and_reserve_when_fully_available():
    import time
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    accounts = _accounts()
    feed._pools["poolA"] = accounts
    feed._sol_usd = 150.0
    feed._updated_at["poolA"] = time.time()
    # 1_000_000 base units at 6 decimals = 1.0 base token; 2_000_000_000 quote units at 9 decimals = 2.0 SOL.
    feed._amounts[accounts.pool_base_token_account] = 1_000_000
    feed._amounts[accounts.pool_quote_token_account] = 2_000_000_000
    snap = feed.get_snapshot("poolA")
    assert snap.available is True
    assert snap.price_usd == pytest.approx(2.0 * 150.0)  # (2.0 SOL / 1.0 base) * 150 usd/sol
    assert snap.reserve_usd == pytest.approx(2.0 * 2.0 * 150.0)
    assert snap.dex_id == "pumpswap"


def test_get_snapshot_zero_base_reserve_unavailable():
    import time
    feed = pumpswap_ws.PumpSwapWebSocketFeed()
    accounts = _accounts()
    feed._pools["poolA"] = accounts
    feed._sol_usd = 150.0
    feed._updated_at["poolA"] = time.time()
    feed._amounts[accounts.pool_base_token_account] = 0
    feed._amounts[accounts.pool_quote_token_account] = 2_000_000_000
    snap = feed.get_snapshot("poolA")
    assert snap.available is False
    assert snap.error == "zero_base_reserve"


# --- add_pools: resolves via injected http client, queues subscriptions -----

@pytest.mark.asyncio
async def test_add_pools_resolves_and_queues_subscriptions():
    base_mint, quote_mint = _pk_bytes("addbase"), _pk_bytes("addquote")
    base_ta, quote_ta = _pk_bytes("addbase_ta"), _pk_bytes("addquote_ta")
    pool_addr = "poolAdd"
    accounts = {
        pool_addr: _build_pool_raw(base_mint=base_mint, quote_mint=quote_mint, base_ta=base_ta, quote_ta=quote_ta),
        pumpswap_ws._pubkey_from_bytes(base_ta): _build_token_account_raw(mint=base_mint, amount=1),
        pumpswap_ws._pubkey_from_bytes(quote_ta): _build_token_account_raw(mint=quote_mint, amount=1),
        pumpswap_ws._pubkey_from_bytes(base_mint): _build_mint_raw(6),
        pumpswap_ws._pubkey_from_bytes(quote_mint): _build_mint_raw(9),
    }
    transport = _mock_transport(accounts)
    feed = pumpswap_ws.PumpSwapWebSocketFeed(http_client_factory=lambda: httpx.AsyncClient(transport=transport))

    added = await feed.add_pools([pool_addr])
    assert added == 1
    assert pool_addr in feed.tracked_pools()
    assert len(feed._pending_subscribe) == 2

    # Calling again with the same pool must not re-resolve/duplicate.
    added_again = await feed.add_pools([pool_addr])
    assert added_again == 0


@pytest.mark.asyncio
async def test_add_pools_best_effort_on_resolution_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")
    transport = httpx.MockTransport(handler)
    feed = pumpswap_ws.PumpSwapWebSocketFeed(http_client_factory=lambda: httpx.AsyncClient(transport=transport))
    added = await feed.add_pools(["poolX"])
    assert added == 0  # never raises


# --- start/stop + one real notification through a fake websocket -----------

class FakeWebSocket:
    """Mimics the subset of the `websockets` connection interface this
    module uses: async `send`, async `recv`, and use as an async context
    manager. `send`d subscribe requests are auto-confirmed with an
    incrementing subscription id; `push_notification` lets a test inject a
    real accountNotification frame at will."""

    def __init__(self):
        self._pending_requests: asyncio.Queue = asyncio.Queue()
        self._extra_incoming: asyncio.Queue = asyncio.Queue()
        self._next_sub_id = 1
        self.sent: list[dict] = []

    async def send(self, msg: str) -> None:
        req = json.loads(msg)
        self.sent.append(req)
        await self._pending_requests.put(req)

    async def recv(self) -> str:
        if not self._extra_incoming.empty():
            return await self._extra_incoming.get()
        get_pending = asyncio.ensure_future(self._pending_requests.get())
        get_extra = asyncio.ensure_future(self._extra_incoming.get())
        done, pending = await asyncio.wait(
            {get_pending, get_extra}, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if get_extra in done:
            return get_extra.result()
        req = get_pending.result()
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        return json.dumps({"jsonrpc": "2.0", "result": sub_id, "id": req["id"]})

    async def push_notification(self, sub_id: int, token_account_raw: bytes) -> None:
        await self._extra_incoming.put(json.dumps({
            "jsonrpc": "2.0",
            "method": "accountNotification",
            "params": {
                "subscription": sub_id,
                "result": {"context": {"slot": 1}, "value": {"data": [_b64(token_account_raw), "base64"]}},
            },
        }))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_feed_start_subscribes_and_applies_a_real_notification(monkeypatch):
    accounts = _accounts()
    ws = FakeWebSocket()

    async def _fake_get_simple_price(coin_ids, *, vs_currencies=None):
        return SimplePriceResult(prices={"solana": {"usd": 150.0}}, available=True)

    monkeypatch.setattr(pumpswap_ws.coingecko_client, "get_simple_price", _fake_get_simple_price)

    feed = pumpswap_ws.PumpSwapWebSocketFeed(connect_fn=lambda url: ws, max_staleness_seconds=1000.0)
    feed._pools["poolA"] = accounts
    feed._token_account_to_pool[accounts.pool_base_token_account] = "poolA"
    feed._token_account_to_pool[accounts.pool_quote_token_account] = "poolA"

    await feed.start()
    try:
        # Let the background task connect + send the 2 subscribe requests.
        for _ in range(20):
            if len(ws.sent) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(ws.sent) == 2

        # The 2 subscribe confirmations are consumed internally by the
        # background loop before it starts reading real notifications --
        # give it a moment, then push a real accountNotification using the
        # FIRST subscription id handed out (sub_id=1, base token account,
        # since it's subscribed first in _all_token_accounts()).
        await asyncio.sleep(0.05)
        base_raw = _build_token_account_raw(mint=_pk_bytes("base"), amount=1_000_000)
        await ws.push_notification(1, base_raw)
        quote_raw = _build_token_account_raw(mint=_pk_bytes("quote"), amount=2_000_000_000)
        await ws.push_notification(2, quote_raw)

        snap = None
        for _ in range(50):
            snap = feed.get_snapshot("poolA")
            if snap.available:
                break
            await asyncio.sleep(0.02)
        assert snap is not None and snap.available is True
        assert snap.price_usd == pytest.approx(2.0 * 150.0)
    finally:
        await feed.stop()


@pytest.mark.asyncio
async def test_feed_stop_is_clean_and_idempotent():
    ws = FakeWebSocket()
    feed = pumpswap_ws.PumpSwapWebSocketFeed(connect_fn=lambda url: ws)
    await feed.start()
    await feed.stop()
    await feed.stop()  # must not raise on a second stop
