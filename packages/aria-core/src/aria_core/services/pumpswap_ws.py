"""PumpSwap real-time reserve feed via Solana native ``accountSubscribe``
websocket push (19/08, promoted from two scratchpad observation scripts --
``pumpswap_ws_latency_probe.py``/``pumpswap_live_pnl_probe.py``, both run
manually against the real prod DB and real public RPC before this module
existed). Decode logic, offsets, and self-verification method are UNCHANGED
from those scripts -- never reinvented here.

**Why this exists**: a real measurement (20 cumulative minutes of live
observation, 19/08) found a median 413ms detection latency via this
websocket push, vs. the ``EXIT_TRACKING_CADENCE_SECONDS=60`` REST polling
cadence ``solana-robinhood-shadow/shadow_persistent.py`` currently uses for
every shadow pocket (~145x). A concrete real case (position "OTTER") sat
8min30 with zero polling check while the websocket already saw the price
collapsing live. First consumer: ``solana_fresh_launch_ws_exit_shadow.py``.

**Method** (unchanged from the scratchpad probes, see
``pumpswap_ws_latency_probe.py``'s own docstring for the full original
writeup): a PumpSwap ``Pool`` account does not carry its reserves as a plain
numeric field -- it stores ``pool_base_token_account``/
``pool_quote_token_account`` (two SPL Token account pubkeys) whose ``amount``
field is what actually moves on every buy/sell. This module:
  1. Decodes a ``Pool`` account using field offsets read from the OFFICIAL
     Anchor IDL (``github.com/pump-fun/pump-public-docs/idl/pump_amm.json``,
     fetched live 19/08 -- never hand-guessed).
  2. Self-verifies the decode: fetches the two derived token accounts and
     checks their ``mint`` matches the Pool's own ``base_mint``/
     ``quote_mint`` -- a pool that fails this check is silently excluded,
     never reported with a fabricated price (confirmed 16/16 mint-match live
     19/08 in the originating probe).
  3. Subscribes (one shared websocket, never one connection per pool) to
     both token accounts and updates an in-memory snapshot on every
     ``accountNotification``.

**Coverage limitation, stated honestly (never silently assumed complete)**:
USD pricing only works for a WSOL-quoted pool (single SOL/USD calibration
via ``coingecko_client``). A real sample taken while building the
originating probe found the MAJORITY of the freshest open PumpSwap positions
(7/8 on one sample) were quoted against a Token-2022 asset, not WSOL --
``PumpSwapLiveSnapshot.available`` is honestly ``False`` for those, never a
guessed price. This module also covers PumpSwap ONLY (the
``pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`` program) -- Raydium/Meteora/
other Solana AMMs are NOT decoded here; a caller must keep its own REST
polling fallback for any pool this feed reports unavailable for, whatever
the reason (non-PumpSwap, non-WSOL-quoted, feed not yet caught up, feed
disconnected). ``PumpSwapWebSocketFeed.get_snapshot`` is designed exactly
for that -- ``available=False`` is the caller's unambiguous fallback signal.

Read-only throughout: no signature, no trade, no write to any ARIA-owned
persistence from this module itself (the caller decides what to persist)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import time
from dataclasses import dataclass

import base58
import httpx

from aria_core.services.coingecko import coingecko_client

logger = logging.getLogger(__name__)

PUMPSWAP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPSWAP_POOL_DISCRIMINATOR = bytes([241, 154, 109, 4, 17, 177, 109, 188])

# Offsets from the OFFICIAL Anchor IDL (pump-fun/pump-public-docs,
# idl/pump_amm.json), fetched live 19/08 and self-verified 16/16 mint-match
# against real on-chain accounts -- never hand-guessed. UNCHANGED from
# pumpswap_ws_latency_probe.py/pumpswap_live_pnl_probe.py.
OFF_POOL_BASE_MINT = 43
OFF_POOL_QUOTE_MINT = 75
OFF_POOL_BASE_TOKEN_ACCOUNT = 139
OFF_POOL_QUOTE_TOKEN_ACCOUNT = 171

WSOL_MINT = "So11111111111111111111111111111111111111112"

# Overridable for a future non-default RPC (e.g. a paid endpoint) without a
# code change -- defaults to the same free public RPC verified live 19/08
# (solana.com/docs/references/clusters: 40 req/10s per method / 100 req/10s
# overall per IP, respected throughout this module's setup calls).
RPC_HTTP_DEFAULT = os.environ.get("ARIA_SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
RPC_WS_DEFAULT = os.environ.get("ARIA_SOLANA_RPC_WS", "wss://api.mainnet-beta.solana.com")

SETUP_REQUEST_GAP_SECONDS = 0.4  # keeps sequential setup calls well under the verified ceiling above

# A pool whose last accountNotification is older than this is treated as
# stale by get_snapshot() -- the caller must fall back to REST polling
# rather than act on a feed that may have silently stopped receiving pushes
# (a real disconnect without a clean close, or simply a pool with genuinely
# zero trading activity -- this module can't fully distinguish the two, so
# age past this bound is the honest, conservative signal either way).
DEFAULT_MAX_STALENESS_SECONDS = 30.0

SOL_USD_CALIBRATION_REFRESH_SECONDS = 240.0

_SUBSCRIBE_CONFIRM_TIMEOUT_SECONDS = 15.0
_RECV_POLL_TIMEOUT_SECONDS = 5.0
_RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0


def _pubkey_from_bytes(raw: bytes) -> str:
    return base58.b58encode(raw).decode()


@dataclass(frozen=True)
class PumpSwapPoolAccounts:
    """Fully decoded + self-verified reserve-tracking accounts for one
    PumpSwap pool. Only ever constructed by ``resolve_pool_accounts`` after
    every self-check has passed -- never partially built."""

    pool_address: str
    base_mint: str
    quote_mint: str
    pool_base_token_account: str
    pool_quote_token_account: str
    base_decimals: int
    quote_decimals: int


@dataclass
class PumpSwapLiveSnapshot:
    """What ``PumpSwapWebSocketFeed.get_snapshot`` hands back to a caller --
    same shape discipline as every other snapshot dataclass in this dome:
    ``available=False`` is the honest, explicit "don't use this" signal,
    never a fabricated/stale number passed off as live."""

    pool_address: str
    price_usd: float | None = None
    reserve_usd: float | None = None
    dex_id: str | None = "pumpswap"
    updated_at: float | None = None  # time.time() of the last applied accountNotification
    available: bool = False
    error: str | None = None


def decode_pool_account(raw: bytes) -> dict[str, str] | None:
    """Returns the 4 pubkeys this module needs from a raw ``Pool`` account,
    or ``None`` if the account is too short or its discriminator doesn't
    match (never a partial/guessed result)."""
    if len(raw) < OFF_POOL_QUOTE_TOKEN_ACCOUNT + 32:
        return None
    if raw[:8] != PUMPSWAP_POOL_DISCRIMINATOR:
        return None
    return {
        "base_mint": _pubkey_from_bytes(raw[OFF_POOL_BASE_MINT:OFF_POOL_BASE_MINT + 32]),
        "quote_mint": _pubkey_from_bytes(raw[OFF_POOL_QUOTE_MINT:OFF_POOL_QUOTE_MINT + 32]),
        "pool_base_token_account": _pubkey_from_bytes(
            raw[OFF_POOL_BASE_TOKEN_ACCOUNT:OFF_POOL_BASE_TOKEN_ACCOUNT + 32]
        ),
        "pool_quote_token_account": _pubkey_from_bytes(
            raw[OFF_POOL_QUOTE_TOKEN_ACCOUNT:OFF_POOL_QUOTE_TOKEN_ACCOUNT + 32]
        ),
    }


def decode_token_account(raw: bytes) -> tuple[str, int] | None:
    """Standard SPL Token Account layout (165 bytes): mint(32) + owner(32) +
    amount(u64 LE, offset 64) -- fixed, unrelated to PumpSwap's own IDL.
    Returns ``(mint, amount)`` or ``None`` if too short to decode."""
    if len(raw) < 72:
        return None
    mint = _pubkey_from_bytes(raw[0:32])
    (amount,) = struct.unpack_from("<Q", raw, 64)
    return mint, amount


def decode_mint_decimals(raw: bytes) -> int | None:
    """Standard SPL Token Program Mint layout: mint_authority Option(36) +
    supply u64(8) + decimals u8(1) at offset 44."""
    if len(raw) < 45:
        return None
    return raw[44]


async def _rpc_get_multiple_accounts(
    http_client: httpx.AsyncClient, rpc_http_url: str, pubkeys: list[str],
) -> list[dict | None]:
    if not pubkeys:
        return []
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleAccounts",
        "params": [pubkeys, {"encoding": "base64", "commitment": "confirmed"}],
    }
    resp = await http_client.post(rpc_http_url, json=payload, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]["value"]


async def resolve_pool_accounts(
    http_client: httpx.AsyncClient, pool_addresses: list[str], *, rpc_http_url: str = RPC_HTTP_DEFAULT,
) -> dict[str, PumpSwapPoolAccounts]:
    """Fetches + decodes + self-verifies every account this module needs for
    each ``pool_address``, in 3 batched ``getMultipleAccounts`` calls
    (never one call per pool). A pool that fails ANY step (not a real
    PumpSwap Pool account, a token-account mint mismatch, undecodable mint
    decimals) is silently excluded from the returned dict -- never a
    half-verified result handed to a caller. Same self-verification method
    as the originating scratchpad probes, unchanged."""
    if not pool_addresses:
        return {}

    pool_raws = await _rpc_get_multiple_accounts(http_client, rpc_http_url, pool_addresses)
    await asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)

    decoded_pools: dict[str, dict[str, str]] = {}
    for pool_addr, acc in zip(pool_addresses, pool_raws):
        if acc is None:
            continue
        raw = base64.b64decode(acc["data"][0])
        decoded = decode_pool_account(raw)
        if decoded is not None:
            decoded_pools[pool_addr] = decoded
    if not decoded_pools:
        return {}

    token_accounts: list[str] = []
    token_account_meta: dict[str, dict[str, str]] = {}
    for pool_addr, d in decoded_pools.items():
        for key, expected_mint in (
            ("pool_base_token_account", d["base_mint"]),
            ("pool_quote_token_account", d["quote_mint"]),
        ):
            ta = d[key]
            token_accounts.append(ta)
            token_account_meta[ta] = {"pool_address": pool_addr, "expected_mint": expected_mint}

    token_raws = await _rpc_get_multiple_accounts(http_client, rpc_http_url, token_accounts)
    await asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)

    verified_pools: set[str] = set(decoded_pools.keys())
    for ta, acc in zip(token_accounts, token_raws):
        meta = token_account_meta[ta]
        ok = False
        if acc is not None:
            raw = base64.b64decode(acc["data"][0])
            dta = decode_token_account(raw)
            if dta is not None and dta[0] == meta["expected_mint"]:
                ok = True
        if not ok:
            verified_pools.discard(meta["pool_address"])
            logger.info(
                "pumpswap_ws: token account %s failed mint self-verification for pool %s -- excluded",
                ta, meta["pool_address"],
            )
    if not verified_pools:
        return {}

    unique_mints = sorted(
        {decoded_pools[p]["base_mint"] for p in verified_pools}
        | {decoded_pools[p]["quote_mint"] for p in verified_pools}
    )
    mint_raws = await _rpc_get_multiple_accounts(http_client, rpc_http_url, unique_mints)
    await asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)
    decimals_by_mint: dict[str, int] = {}
    for mint, acc in zip(unique_mints, mint_raws):
        if acc is None:
            continue
        raw = base64.b64decode(acc["data"][0])
        dec = decode_mint_decimals(raw)
        if dec is not None:
            decimals_by_mint[mint] = dec

    result: dict[str, PumpSwapPoolAccounts] = {}
    for pool_addr in verified_pools:
        d = decoded_pools[pool_addr]
        base_dec = decimals_by_mint.get(d["base_mint"])
        quote_dec = decimals_by_mint.get(d["quote_mint"])
        if base_dec is None or quote_dec is None:
            logger.info("pumpswap_ws: missing decimals for pool %s -- excluded", pool_addr)
            continue
        result[pool_addr] = PumpSwapPoolAccounts(
            pool_address=pool_addr,
            base_mint=d["base_mint"], quote_mint=d["quote_mint"],
            pool_base_token_account=d["pool_base_token_account"],
            pool_quote_token_account=d["pool_quote_token_account"],
            base_decimals=base_dec, quote_decimals=quote_dec,
        )
    return result


class PumpSwapWebSocketFeed:
    """Maintains ONE persistent websocket connection subscribed to the
    reserve token accounts of a set of PumpSwap pools, updating an in-memory
    price/reserve snapshot per pool on every real ``accountNotification``.
    Auto-reconnects with exponential backoff on any disconnect. Never a
    single point of failure for a caller: ``get_snapshot`` returns
    ``available=False`` for any pool this feed can't currently price
    (unresolved, non-WSOL-quoted, stale, or the feed itself never
    connected) -- the caller's own REST-polling fallback is what makes this
    safe, not anything inside this class.

    ``connect_fn``/``http_client_factory`` are injectable purely for tests
    (never touch the real network) -- production code never needs to pass
    them."""

    def __init__(
        self,
        *,
        rpc_ws_url: str = RPC_WS_DEFAULT,
        rpc_http_url: str = RPC_HTTP_DEFAULT,
        max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS,
        connect_fn=None,
        http_client_factory=None,
    ) -> None:
        self._rpc_ws_url = rpc_ws_url
        self._rpc_http_url = rpc_http_url
        self._max_staleness_seconds = max_staleness_seconds
        self._connect_fn = connect_fn
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient())

        self._pools: dict[str, PumpSwapPoolAccounts] = {}
        self._token_account_to_pool: dict[str, str] = {}
        self._amounts: dict[str, int] = {}
        self._updated_at: dict[str, float] = {}
        self._pending_subscribe: list[str] = []

        self._sol_usd: float | None = None
        self._last_calibration_at: float = 0.0

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # --- pool management ------------------------------------------------

    async def add_pools(self, pool_addresses: list[str]) -> int:
        """Resolves + self-verifies any NEW pool addresses (already-tracked
        ones are skipped, never re-resolved) and queues their token
        accounts for subscription -- picked up on the current connection
        (if the read loop is running) or on the next connect. Returns how
        many pools were newly added. Best-effort: a resolution failure
        (network error, no verified pool in this batch) never raises."""
        new_addresses = [p for p in pool_addresses if p not in self._pools]
        if not new_addresses:
            return 0
        try:
            async with self._http_client_factory() as http_client:
                resolved = await resolve_pool_accounts(http_client, new_addresses, rpc_http_url=self._rpc_http_url)
        except Exception as exc:  # noqa: BLE001 -- resolution must never raise into the caller
            logger.info("pumpswap_ws: add_pools resolution failed (%s)", exc)
            return 0
        for pool_addr, accounts in resolved.items():
            self._pools[pool_addr] = accounts
            self._token_account_to_pool[accounts.pool_base_token_account] = pool_addr
            self._token_account_to_pool[accounts.pool_quote_token_account] = pool_addr
            self._pending_subscribe.append(accounts.pool_base_token_account)
            self._pending_subscribe.append(accounts.pool_quote_token_account)
        return len(resolved)

    def tracked_pools(self) -> list[str]:
        return list(self._pools.keys())

    # --- lifecycle --------------------------------------------------------

    async def start(self, pool_addresses: list[str] | None = None) -> None:
        """Resolves the initial pool set (if any) and spawns the background
        reconnect/read loop. Safe to call ``add_pools`` afterward while
        running."""
        if pool_addresses:
            await self.add_pools(pool_addresses)
        self._stop_event.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 -- shutdown must never raise
                pass
            self._task = None

    # --- read side ----------------------------------------------------------

    def get_snapshot(self, pool_address: str) -> PumpSwapLiveSnapshot:
        accounts = self._pools.get(pool_address)
        if accounts is None:
            return PumpSwapLiveSnapshot(pool_address=pool_address, available=False, error="pool_not_tracked")

        updated_at = self._updated_at.get(pool_address)
        if updated_at is None:
            return PumpSwapLiveSnapshot(pool_address=pool_address, available=False, error="no_notification_yet")
        if (time.time() - updated_at) > self._max_staleness_seconds:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="stale",
            )

        if accounts.quote_mint != WSOL_MINT:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at,
                error="non_wsol_quote_unsupported",
            )
        if self._sol_usd is None:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="no_sol_usd_calibration",
            )

        base_amt = self._amounts.get(accounts.pool_base_token_account)
        quote_amt = self._amounts.get(accounts.pool_quote_token_account)
        if base_amt is None or quote_amt is None:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="reserves_not_seen_yet",
            )

        base_tokens = base_amt / (10 ** accounts.base_decimals)
        quote_tokens = quote_amt / (10 ** accounts.quote_decimals)
        if base_tokens <= 0:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="zero_base_reserve",
            )

        price_usd = (quote_tokens / base_tokens) * self._sol_usd
        # Approximation documented in solana_pump_shadow.py's own
        # ``_apply_price_impact_and_fee`` docstring (``depth = reserve_usd /
        # 2``, since only one side's true depth is known there either): a
        # roughly-balanced constant-product pool has both sides at similar
        # fair value, so total reserve_usd ~= 2x the quote side's USD value.
        reserve_usd = 2.0 * quote_tokens * self._sol_usd
        return PumpSwapLiveSnapshot(
            pool_address=pool_address, price_usd=price_usd, reserve_usd=reserve_usd,
            dex_id="pumpswap", updated_at=updated_at, available=True,
        )

    # --- background loop --------------------------------------------------

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._rpc_ws_url)
        import websockets

        return websockets.connect(self._rpc_ws_url, ping_interval=20, ping_timeout=20)

    async def _all_token_accounts(self) -> list[str]:
        accounts: list[str] = []
        for a in self._pools.values():
            accounts.append(a.pool_base_token_account)
            accounts.append(a.pool_quote_token_account)
        return accounts

    async def _subscribe_and_confirm(self, ws, token_accounts: list[str]) -> dict[int, str]:
        if not token_accounts:
            return {}
        local_id_to_account: dict[int, str] = {}
        base_id = int(time.time() * 1000) % 1_000_000_000
        for i, ta in enumerate(token_accounts):
            local_id = base_id + i
            local_id_to_account[local_id] = ta
            req = {
                "jsonrpc": "2.0", "id": local_id, "method": "accountSubscribe",
                "params": [ta, {"encoding": "base64", "commitment": "confirmed"}],
            }
            await ws.send(json.dumps(req))
            await asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)

        confirmed: dict[int, str] = {}
        deadline = time.time() + _SUBSCRIBE_CONFIRM_TIMEOUT_SECONDS
        pending = set(local_id_to_account)
        while pending and time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in msg and msg["id"] in pending:
                pending.discard(msg["id"])
                if "result" in msg:
                    confirmed[msg["result"]] = local_id_to_account[msg["id"]]
                else:
                    logger.info(
                        "pumpswap_ws: subscribe failed for %s (%s)",
                        local_id_to_account[msg["id"]], msg.get("error"),
                    )
        for local_id in pending:
            logger.info(
                "pumpswap_ws: no subscribe confirmation before deadline for %s",
                local_id_to_account[local_id],
            )
        return confirmed

    async def _maybe_refresh_calibration(self) -> None:
        now = time.time()
        if self._sol_usd is not None and (now - self._last_calibration_at) < SOL_USD_CALIBRATION_REFRESH_SECONDS:
            return
        try:
            result = await coingecko_client.get_simple_price(["solana"], vs_currencies=["usd"])
            if result.available:
                price = result.prices.get("solana", {}).get("usd")
                if price is not None:
                    self._sol_usd = price
        except Exception as exc:  # noqa: BLE001 -- calibration is best-effort, never fatal
            logger.info("pumpswap_ws: SOL/USD calibration failed (%s)", exc)
        finally:
            self._last_calibration_at = now

    def _apply_notification(self, msg: dict, sub_id_to_account: dict[int, str]) -> None:
        params = msg.get("params")
        if not params:
            return
        sub_id = params.get("subscription")
        ta = sub_id_to_account.get(sub_id)
        if ta is None:
            return
        value = params.get("result", {}).get("value")
        if not value or not value.get("data"):
            return
        raw = base64.b64decode(value["data"][0])
        decoded = decode_token_account(raw)
        if decoded is None:
            return
        _, amount = decoded
        self._amounts[ta] = amount
        pool_address = self._token_account_to_pool.get(ta)
        if pool_address:
            self._updated_at[pool_address] = time.time()

    async def _read_loop(self, ws, sub_id_to_account: dict[int, str]) -> None:
        while not self._stop_event.is_set():
            if self._pending_subscribe:
                newly = self._pending_subscribe
                self._pending_subscribe = []
                new_confirmed = await self._subscribe_and_confirm(ws, newly)
                sub_id_to_account.update(new_confirmed)

            await self._maybe_refresh_calibration()

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_POLL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("method") != "accountNotification":
                continue
            self._apply_notification(msg, sub_id_to_account)

    async def _run_loop(self) -> None:
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            try:
                async with self._connect() as ws:
                    await self._maybe_refresh_calibration()
                    token_accounts = await self._all_token_accounts()
                    self._pending_subscribe = [
                        ta for ta in self._pending_subscribe if ta not in token_accounts
                    ]  # avoid a double-subscribe of accounts already covered by the fresh full set
                    sub_id_to_account = await self._subscribe_and_confirm(ws, token_accounts)
                    backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
                    await self._read_loop(ws, sub_id_to_account)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a single connection error must never kill the feed
                logger.info("pumpswap_ws: feed loop error (%s) -- reconnecting in %.1fs", exc, backoff)
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
