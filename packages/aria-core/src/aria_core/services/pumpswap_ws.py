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
# 20/08, operator decision: "detruit tout ces chemins vers les API et le RPC
# Solana public, au moins on est sur de notre coup" -- there is NO public
# fallback any more. The free endpoint used to be the default value, so a
# process missing `ARIA_SOLANA_RPC_*` silently degraded onto the tightest
# per-IP limits; that is how the dome's busiest subscription (~6650 trades per
# 100s) shipped on the public RPC while the paid one sat unused.
#
# Empty rather than raising AT IMPORT on purpose: these modules are imported by
# the whole test suite and by tools that never touch Solana, so a module-level
# raise would break unrelated things over a config issue. The failure surfaces
# at the moment a Solana call is actually attempted, via
# `require_solana_rpc_http()`/`require_solana_rpc_ws()` below -- loud, named,
# and impossible to mistake for a working degraded mode.
RPC_HTTP_DEFAULT = (os.environ.get("ARIA_SOLANA_RPC_HTTP", "") or "").strip()
RPC_WS_DEFAULT = (os.environ.get("ARIA_SOLANA_RPC_WS", "") or "").strip()

_RPC_MISSING_MSG = (
    "Solana RPC not configured: set {var} to the dedicated endpoint. There is NO "
    "public fallback by design (operator decision 20/08) -- running on the free "
    "public RPC silently throttled the busiest feed while the paid endpoint went "
    "unused. If the dedicated RPC is down, point this variable at another one."
)


def solana_rpc_is_dedicated() -> bool:
    """True when both endpoints are configured. There is no public fallback, so
    "configured" and "dedicated" are now the same thing."""
    return bool(RPC_HTTP_DEFAULT) and bool(RPC_WS_DEFAULT)


# 22/08, real outage: Helius' monthly quota ran out and answered 429 to
# everything. Every caller of require_solana_rpc_http() failed at once --
# selling, pricing, pool resolution -- and the pocket went three hours without
# a trade while a fully working second provider sat unused.
#
# One provider being exhausted must not be a system-wide failure, so the
# fallback lives HERE, at the single point every caller already goes through,
# rather than in each of them. `_rpc_http_exhausted_until` is set by whoever
# actually sees the 429; nothing here probes on its own.
_QUOTA_BACKOFF_SECONDS = 600.0
_rpc_http_exhausted_until = 0.0


def note_rpc_http_exhausted() -> None:
    """Called when the primary endpoint answers 429 on quota."""
    global _rpc_http_exhausted_until
    import time as _time

    _rpc_http_exhausted_until = _time.monotonic() + _QUOTA_BACKOFF_SECONDS
    logger.warning(
        "pumpswap_ws: primary Solana RPC exhausted, falling back for %.0fs",
        _QUOTA_BACKOFF_SECONDS,
    )


def require_solana_rpc_http() -> str:
    """The HTTP endpoint to use right now.

    Prefers the primary, but hands out the polling endpoint while the primary
    is known exhausted -- a degraded provider is worth more than none.
    """
    import os
    import time as _time

    if _rpc_http_exhausted_until > _time.monotonic():
        fallback = (os.environ.get("ARIA_SOLANA_RPC_HTTP_POLLING", "") or "").strip()
        if fallback:
            return fallback
    if not RPC_HTTP_DEFAULT:
        fallback = (os.environ.get("ARIA_SOLANA_RPC_HTTP_POLLING", "") or "").strip()
        if fallback:
            return fallback
        raise RuntimeError(_RPC_MISSING_MSG.format(var="ARIA_SOLANA_RPC_HTTP"))
    return RPC_HTTP_DEFAULT


def require_solana_rpc_ws() -> str:
    if not RPC_WS_DEFAULT:
        raise RuntimeError(_RPC_MISSING_MSG.format(var="ARIA_SOLANA_RPC_WS"))
    return RPC_WS_DEFAULT

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

# 20/08, same real incident and fix as pumpfun_bonding_ws.py's own constant
# of this name (see its docstring) -- this module subscribes to TWO token
# accounts per pool (base+quote), so it is even more exposed to the same
# one-at-a-time-with-a-blocking-gap send pattern blowing past ping_timeout
# on a real reconnect-resubscribe-all-tracked-pools cycle.
_SUBSCRIBE_BATCH_SIZE = 40
_SUBSCRIBE_BATCH_GAP_SECONDS = 1.0


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
    never a fabricated/stale number passed off as live.

    ``price_high_since_last_read``/``price_low_since_last_read`` (19/08):
    see ``PumpFunBondingLiveSnapshot``'s own docstring in
    ``pumpfun_bonding_ws.py`` for the full rationale -- same mechanism here,
    lets a caller skip a REST OHLCV call entirely for a websocket-priced
    row."""

    pool_address: str
    price_usd: float | None = None
    reserve_usd: float | None = None
    dex_id: str | None = "pumpswap"
    updated_at: float | None = None  # time.time() of the last applied accountNotification
    available: bool = False
    error: str | None = None
    price_high_since_last_read: float | None = None
    price_low_since_last_read: float | None = None
    # 19/08 -- see PumpFunBondingLiveSnapshot's own docstring for the full
    # rationale. True when priced from a quiet-but-connected account.
    stale: bool = False


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
    # No public fallback by design: an unset endpoint fails here, named,
    # rather than silently sending the dome's Solana reads to the free RPC.
    resp = await http_client.post(rpc_http_url or require_solana_rpc_http(), json=payload, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]["value"]


async def find_pool_for_mint(
    http_client: httpx.AsyncClient, mint: str, *, rpc_http_url: str = RPC_HTTP_DEFAULT,
) -> str | None:
    """The PumpSwap pool holding this token, resolved through the RPC alone.

    21/08, operator: "on a dit tout par le RPC Helius". A pump.fun position
    keeps its BONDING-CURVE address for its whole life, so once the token
    graduates the AMM pool is simply unknown and the pocket fell back to the
    rate-limited REST cascade -- for the segment that performs BEST (+161% on
    the historical sample). This closes that hole without a single third-party
    call.

    Deliberately a `getProgramAccounts` filtered on `base_mint` rather than a
    PDA derivation. The pool PDA is documented as
    ``["pool", index, creator, base_mint, quote_mint]``, but for a MIGRATED
    pool the `creator` seed is not the token's creator: derivation was tested
    against three real migrated pools with the token creator at index 0 and 1,
    and matched none of them. Guessing the right authority would be exactly
    the kind of unverified assumption that produces a plausible wrong address,
    so this asks the chain instead of inferring.

    Called ONCE per position, at graduation -- after which the pool is handed
    to the websocket feed and costs nothing further.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            PUMPSWAP_PROGRAM_ID,
            {
                "encoding": "base64",
                "commitment": "confirmed",
                "filters": [
                    {"memcmp": {"offset": 0, "bytes": base58.b58encode(PUMPSWAP_POOL_DISCRIMINATOR).decode()}},
                    {"memcmp": {"offset": OFF_POOL_BASE_MINT, "bytes": mint}},
                ],
            },
        ],
    }
    try:
        resp = await http_client.post(
            rpc_http_url or require_solana_rpc_http(), json=payload, timeout=20.0
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return None
        accounts = data.get("result") or []
    except Exception:  # noqa: BLE001 -- resolution is an enhancement, never a hard requirement
        return None

    # Several pools can share a base mint (a second one can be created on the
    # same token). Keep the one whose quote side is WSOL, which is what a
    # pump.fun migration always produces, rather than the first row returned.
    for entry in accounts:
        try:
            raw = base64.b64decode(entry["account"]["data"][0])
        except Exception:  # noqa: BLE001
            continue
        decoded = decode_pool_account(raw)
        if decoded and decoded.get("quote_mint") == WSOL_MINT:
            return entry.get("pubkey")
    return accounts[0].get("pubkey") if accounts else None


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
        self._pending_unsubscribe: list[int] = []

        # 19/08 -- high/low tracked across EVERY notification since the
        # caller's last read, reset by get_snapshot() itself -- see
        # PumpSwapLiveSnapshot's own docstring.
        self._price_high_since_read: dict[str, float] = {}
        self._price_low_since_read: dict[str, float] = {}

        # 19/08 -- same real-incident fix as pumpfun_bonding_ws.py's own
        # remove_pools (see that method's own docstring): tracks the active
        # accountSubscribe id for each TOKEN ACCOUNT (two per pool -- base +
        # quote), so remove_pools() can send real accountUnsubscribe calls
        # instead of leaving stale subscriptions accumulating forever.
        self._account_to_sub_id: dict[str, int] = {}
        self._sub_id_to_account: dict[int, str] = {}
        self._ws = None

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

    def remove_pools(self, pool_addresses: list[str]) -> None:
        """Sheds a pool once the caller no longer needs it -- see
        ``pumpfun_bonding_ws.PumpFunBondingWebSocketFeed.remove_pools``'s own
        docstring for the real incident this class of fix addresses. Drops
        BOTH token accounts (base + quote) per pool -- this feed subscribes
        to two accounts per pool, unlike the bonding-curve feed's one."""
        for pool_addr in pool_addresses:
            accounts = self._pools.pop(pool_addr, None)
            self._updated_at.pop(pool_addr, None)
            self._price_high_since_read.pop(pool_addr, None)
            self._price_low_since_read.pop(pool_addr, None)
            if accounts is None:
                continue
            for ta in (accounts.pool_base_token_account, accounts.pool_quote_token_account):
                self._token_account_to_pool.pop(ta, None)
                self._amounts.pop(ta, None)
                sub_id = self._account_to_sub_id.pop(ta, None)
                if sub_id is not None:
                    self._sub_id_to_account.pop(sub_id, None)
                    self._pending_unsubscribe.append(sub_id)
                elif ta in self._pending_subscribe:
                    self._pending_subscribe.remove(ta)

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

        # 19/08 -- see PumpFunBondingWebSocketFeed.get_snapshot's own comment
        # for the full rationale (same real incident, same fix, mirrored
        # here): a quiet account on a LIVE connection means the price hasn't
        # moved, not that the data is unusable. Only force REST-required
        # unavailability while genuinely disconnected.
        is_stale = (time.time() - updated_at) > self._max_staleness_seconds
        if is_stale and self._ws is None:
            return PumpSwapLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="stale_disconnected",
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

        # High/low since the caller's last read -- see PumpSwapLiveSnapshot's
        # own docstring. Reset AFTER reading so the next window starts fresh.
        price_high = self._price_high_since_read.get(pool_address, price_usd)
        price_low = self._price_low_since_read.get(pool_address, price_usd)
        self._price_high_since_read[pool_address] = price_usd
        self._price_low_since_read[pool_address] = price_usd

        return PumpSwapLiveSnapshot(
            pool_address=pool_address, price_usd=price_usd, reserve_usd=reserve_usd,
            dex_id="pumpswap", updated_at=updated_at, available=True, stale=is_stale,
            price_high_since_last_read=max(price_high, price_usd),
            price_low_since_last_read=min(price_low, price_usd),
        )

    # --- background loop --------------------------------------------------

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._rpc_ws_url)
        # No public fallback by design (see pumpswap_ws's own comment): an
        # unset endpoint fails here, named, rather than silently degrading.
        self._rpc_ws_url = self._rpc_ws_url or require_solana_rpc_ws()
        import websockets

        # ping_timeout raised 20->40s (19/08), same empirical test as
        # pumpfun_bonding_ws.py -- see comment there for the reasoning.
        return websockets.connect(self._rpc_ws_url, ping_interval=20, ping_timeout=40)

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

        # 20/08 -- batched concurrent sends, see _SUBSCRIBE_BATCH_SIZE's own
        # docstring for the real incident this replaces.
        items = list(local_id_to_account.items())
        for batch_start in range(0, len(items), _SUBSCRIBE_BATCH_SIZE):
            batch = items[batch_start:batch_start + _SUBSCRIBE_BATCH_SIZE]
            await asyncio.gather(*(
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": local_id, "method": "accountSubscribe",
                    "params": [ta, {"encoding": "base64", "commitment": "confirmed"}],
                }))
                for local_id, ta in batch
            ))
            if batch_start + _SUBSCRIBE_BATCH_SIZE < len(items):
                await asyncio.sleep(_SUBSCRIBE_BATCH_GAP_SECONDS)

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

    def _price_usd_now(self, pool_address: str) -> float | None:
        """Shared by ``_apply_notification`` (to track high/low across
        notifications) and ``get_snapshot`` (to report the current price) --
        never duplicated. Returns ``None`` for anything not currently
        priceable -- same conditions ``get_snapshot`` itself checks."""
        accounts = self._pools.get(pool_address)
        if accounts is None or accounts.quote_mint != WSOL_MINT or self._sol_usd is None:
            return None
        base_amt = self._amounts.get(accounts.pool_base_token_account)
        quote_amt = self._amounts.get(accounts.pool_quote_token_account)
        if base_amt is None or quote_amt is None:
            return None
        base_tokens = base_amt / (10 ** accounts.base_decimals)
        quote_tokens = quote_amt / (10 ** accounts.quote_decimals)
        if base_tokens <= 0:
            return None
        return (quote_tokens / base_tokens) * self._sol_usd

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
            price = self._price_usd_now(pool_address)
            if price is not None:
                prev_high = self._price_high_since_read.get(pool_address)
                prev_low = self._price_low_since_read.get(pool_address)
                self._price_high_since_read[pool_address] = price if prev_high is None else max(prev_high, price)
                self._price_low_since_read[pool_address] = price if prev_low is None else min(prev_low, price)

    async def _process_pending_unsubscribe(self, ws) -> None:
        if not self._pending_unsubscribe:
            return
        pending = self._pending_unsubscribe
        self._pending_unsubscribe = []
        for sub_id in pending:
            try:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000_000,
                    "method": "accountUnsubscribe", "params": [sub_id],
                }))
            except Exception as exc:  # noqa: BLE001 -- unsubscribe is best-effort, never fatal
                logger.info("pumpswap_ws: accountUnsubscribe send failed for sub_id %s (%s)", sub_id, exc)

    async def _read_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            if self._pending_subscribe:
                newly = self._pending_subscribe
                self._pending_subscribe = []
                new_confirmed = await self._subscribe_and_confirm(ws, newly)
                self._sub_id_to_account.update(new_confirmed)
                for sub_id, ta in new_confirmed.items():
                    self._account_to_sub_id[ta] = sub_id

            await self._process_pending_unsubscribe(ws)
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
            self._apply_notification(msg, self._sub_id_to_account)

    async def _run_loop(self) -> None:
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            try:
                async with self._connect() as ws:
                    self._ws = ws
                    await self._maybe_refresh_calibration()
                    token_accounts = await self._all_token_accounts()
                    self._pending_subscribe = [
                        ta for ta in self._pending_subscribe if ta not in token_accounts
                    ]  # avoid a double-subscribe of accounts already covered by the fresh full set
                    self._pending_unsubscribe = []  # a fresh connection has no stale subscription to shed
                    confirmed = await self._subscribe_and_confirm(ws, token_accounts)
                    self._sub_id_to_account = dict(confirmed)
                    self._account_to_sub_id = {ta: sid for sid, ta in confirmed.items()}
                    backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
                    await self._read_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a single connection error must never kill the feed
                logger.info("pumpswap_ws: feed loop error (%s) -- reconnecting in %.1fs", exc, backoff)
            finally:
                self._ws = None
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
