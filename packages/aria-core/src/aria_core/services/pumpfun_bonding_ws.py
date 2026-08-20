"""PumpFun bonding-curve real-time reserve feed via Solana native
``accountSubscribe`` websocket push (19/08) -- the complement to
``pumpswap_ws.py``: that module can only price a pool AFTER migration to the
PumpSwap AMM (``resolved=0`` verified live on every fresh-launch token still
on the bonding curve, since a bonding-curve account isn't a PumpSwap ``Pool``
account at all). This module reads the bonding-curve account directly, so it
covers a token from creation up to migration -- the exact window
``pumpswap_ws.py`` cannot see. A caller wanting full-lifecycle coverage
checks this feed first, falls back to ``pumpswap_ws.py`` once ``complete``
flips ``True``.

**Program + account layout, from the OFFICIAL Anchor IDL**
(``github.com/pump-fun/pump-public-docs/idl/pump.json``, fetched live
19/08 -- never hand-guessed, same discipline as ``pumpswap_ws.py``):

  - Program ID: ``6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P``
  - ``BondingCurve`` account discriminator: ``[23,183,248,55,96,216,172,96]``
  - Fields (offsets AFTER the 8-byte discriminator), in IDL order:
    ``virtual_token_reserves`` u64@8, ``virtual_quote_reserves`` u64@16,
    ``real_token_reserves`` u64@24, ``real_quote_reserves`` u64@32,
    ``token_total_supply`` u64@40, ``complete`` bool@48, ``creator``
    pubkey@49, ``is_mayhem_mode`` bool@81, ``is_cashback_coin`` bool@82,
    ``quote_mint`` pubkey@83.
  - PDA seeds (from the ``create``/``buy`` instructions' own account list):
    ``["bonding-curve", mint]`` under the program above -- NOT derived by
    this module: PumpPortal's ``subscribeNewToken`` event already hands us
    the resolved address as ``bondingCurveKey`` (see
    ``pumpportal_ws.py``'s own docstring: independently confirmed to be the
    exact ``pool_address`` DexPaprika indexes for the same token), so
    ``add_pools`` takes it as a caller-supplied input rather than computing
    an off-curve PDA by hand.

**Why the caller must supply ``mint`` alongside the bonding-curve address**:
unlike a PumpSwap ``Pool`` account (which stores its own token mints), the
``BondingCurve`` account does NOT carry the token's mint -- the mint is only
the PDA's derivation seed, not a stored field. Resolving the token's real
decimals (needed to turn ``virtual_token_reserves`` into a price) requires
reading the Mint account separately, which requires knowing the mint.
``PumpPortalNewTokenEvent`` already carries both ``bonding_curve_key`` and
``mint`` from the same creation event, so this is never an extra lookup for
an existing caller.

**Price formula**: pump.fun's bonding curve is a constant-product AMM using
VIRTUAL reserves (an initial offset baked in by design to avoid an infinite
price at zero real liquidity) -- ``price_sol_per_token =
virtual_quote_reserves / virtual_token_reserves`` (both in raw units, ratio
cancels the differing decimals scale once each side is normalized). Real
liquidity (for a ``reserve_usd`` figure, never used for pricing itself)
comes from ``real_quote_reserves`` -- the actual SOL depositors put in, as
opposed to the virtual offset.

**Coverage limitation, stated honestly**: only bonding curves quoted in
native SOL get a USD price -- verified live (19/08) that ``quote_mint``
decodes to the System Program's all-zero pubkey
(``11111111111111111111111111111111``, Solana's convention for "native SOL",
NOT ``WSOL_MINT`` -- a bonding curve's quote reserve is raw lamports, never
a wrapped-SOL SPL token) on every real curve tested.
``PumpFunBondingLiveSnapshot.available`` is honestly ``False`` for anything
else (the IDL's ``quote_mint`` field suggests the program may support other
quote assets; never assumed SOL without checking). A curve already flagged
``complete=True`` is also reported unavailable here -- its liquidity has
moved to the AMM, ``pumpswap_ws.py`` is the caller's next stop, never this
feed's own stale virtual-reserve snapshot.

Read-only throughout: no signature, no trade, no write to any ARIA-owned
persistence from this module itself (the caller decides what to persist)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from aria_core.services.coingecko import coingecko_client
from aria_core.services.pumpswap_ws import (
    RPC_HTTP_DEFAULT,
    RPC_WS_DEFAULT,
    _pubkey_from_bytes,
    _rpc_get_multiple_accounts,
    decode_mint_decimals,
)

logger = logging.getLogger(__name__)

PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
BONDING_CURVE_DISCRIMINATOR = bytes([23, 183, 248, 55, 96, 216, 172, 96])

# Offsets from the OFFICIAL Anchor IDL (pump-fun/pump-public-docs,
# idl/pump.json), fetched live 19/08 -- see module docstring.
OFF_VIRTUAL_TOKEN_RESERVES = 8
OFF_VIRTUAL_QUOTE_RESERVES = 16
OFF_REAL_TOKEN_RESERVES = 24
OFF_REAL_QUOTE_RESERVES = 32
OFF_TOKEN_TOTAL_SUPPLY = 40
OFF_COMPLETE = 48
OFF_CREATOR = 49
OFF_QUOTE_MINT = 83
BONDING_CURVE_ACCOUNT_MIN_LEN = OFF_QUOTE_MINT + 32

SOL_DECIMALS = 9

# 19/08, found live testing this module against 3 real freshly-created
# bonding curves: ``quote_mint`` decoded to ``11111111111111111111111111111111``
# (base58 of 32 zero bytes) on all 3, NOT ``WSOL_MINT`` -- the bonding
# curve's quote reserve is native SOL (raw lamports), never a wrapped-SOL
# SPL token like PumpSwap's AMM pool uses. Solana's own convention
# represents "native SOL" with the System Program's all-zero pubkey in a
# quote-mint-style field. Verified via ``base58.b58encode(bytes(32))``,
# never hand-typed.
NATIVE_SOL_QUOTE_MARKER = "11111111111111111111111111111111"

# 20/08 -- how far a token has progressed along its bonding curve, 0.0 at
# creation to 1.0 at graduation. Derived from ``real_token_reserves`` (how many
# of the curve's sale tokens are LEFT), which is price-independent -- unlike a
# USD-liquidity proxy, which drifts with the SOL price and with whatever the
# pocket happened to measure.
#
# Why it matters, operator's question and the data behind it: winrate already
# DOUBLES between the <30%-of-curve band (9.9%, n=1277) and the 30-50% band
# (20.9%, n=239), yet ARIA has only FOUR closures past 50% and four past 75% --
# the sourcing never goes there, so the most promising band in the whole dome
# is entirely unmeasured. This exposes the axis so it can finally be measured.
#
# INITIAL_CURVE_TOKENS is pump.fun's documented 793.1M sale allocation. Treated
# as a PROVISIONAL constant: it is not read from chain, so `bonding_progress`
# self-corrects by clamping and returns None on anything inconsistent rather
# than a fabricated ratio. Recalibrate against the real max observed just
# before `complete` flips once enough graduations are captured.
INITIAL_CURVE_TOKENS = 793_100_000

DEFAULT_MAX_STALENESS_SECONDS = 30.0
SOL_USD_CALIBRATION_REFRESH_SECONDS = 240.0

_SUBSCRIBE_CONFIRM_TIMEOUT_SECONDS = 15.0
# 19/08, lowered 5.0->1.0 (operator speed target: ideally 10-20s from
# creation to confirmed entry) -- this is also how often ``_read_loop``
# checks ``_pending_subscribe`` for a newly-added pool (add_pools() only
# queues it; the actual accountSubscribe send happens on the next pass of
# this loop), so a lower value shortens the worst-case gap between
# add_pools() and the subscription actually going live. Not a network
# cost -- just how often ws.recv() times out and the loop re-checks its
# own local queue.
_RECV_POLL_TIMEOUT_SECONDS = 1.0
_RECONNECT_BACKOFF_INITIAL_SECONDS = 1.0
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0
SETUP_REQUEST_GAP_SECONDS = 0.4

# 20/08, real production incident: ``_subscribe_and_confirm`` used to send
# every ``accountSubscribe`` request ONE AT A TIME with a blocking
# ``asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)`` between each. That constant
# is calibrated for ``resolve_bonding_curves``'s REST ``getMultipleAccounts``
# calls (the verified 40 req/10s ceiling, see ``pumpswap_ws.py``'s own
# docstring) -- fine at a handful of pools, but ``_run_loop`` resubscribes
# to EVERY currently-tracked pool on EVERY reconnect (see its own comment),
# and a real live backlog reached 333 pools on one connection: 333 * 0.4s =
# 133s just to finish SENDING, blowing well past ``ping_timeout=40s`` and
# triggering a fresh reconnect before setup even completed -- which then
# retried the exact same 133s send phase from scratch, a self-sustaining
# reconnect storm that left ``get_snapshot`` unavailable for every tracked
# pool the whole time. Batched, concurrent sends below fix this: no
# officially documented rate ceiling exists for ``accountSubscribe``
# specifically (unlike the REST endpoint above), so this batch size is kept
# at the same conservative order of magnitude as the one REAL verified
# ceiling this module has evidence for, rather than assumed safe at any
# size. 333 pools / 40 per batch = 9 batches * 1.0s = ~9s total, comfortably
# under the 40s ping_timeout even with zero headroom left for anything else.
_SUBSCRIBE_BATCH_SIZE = 40
_SUBSCRIBE_BATCH_GAP_SECONDS = 1.0


@dataclass(frozen=True)
class PumpFunBondingCurveAccount:
    """Fully decoded bonding-curve account for one token -- only ever
    constructed by ``resolve_bonding_curves`` after the discriminator check
    has passed. ``token_decimals`` resolved via a separate Mint-account
    read (the BondingCurve account itself doesn't carry it)."""

    pool_address: str  # the bonding-curve PDA itself (== PumpPortal's bondingCurveKey)
    mint: str
    quote_mint: str
    token_decimals: int
    # 20/08 -- the decoded account fields, kept instead of discarded. This
    # resolver ALREADY decodes the whole account to verify its discriminator,
    # then threw the reserves away, so any caller needing curve position had
    # no way to get it short of a second read. Found live: the LATE-BONDING
    # pocket rejected 88 straight candidates as `blocked_progress_unknown`
    # because of exactly this. Defaults to an empty dict so every existing
    # construction site keeps working unchanged.
    curve: dict = field(default_factory=dict)


@dataclass
class PumpFunBondingLiveSnapshot:
    """Same shape discipline as ``PumpSwapLiveSnapshot``:
    ``available=False`` is the honest, explicit "don't use this" signal.

    ``price_high_since_last_read``/``price_low_since_last_read`` (19/08):
    the high/low seen across EVERY ``accountNotification`` received since
    the caller's last ``get_snapshot`` call for this pool -- lets a caller
    reconstruct the same "did it spike between checks" signal a REST OHLCV
    call provides, without ever calling GeckoTerminal. Real motivation: a
    real cycle was found spending most of its time on a GeckoTerminal OHLCV
    call made ONLY for this purpose, serialized behind that provider's own
    rate limit, even for rows the websocket had already priced. ``None``
    only if no notification landed yet this window."""

    pool_address: str
    price_usd: float | None = None
    reserve_usd: float | None = None
    dex_id: str | None = "pumpfun"
    complete: bool | None = None
    updated_at: float | None = None
    available: bool = False
    error: str | None = None
    price_high_since_last_read: float | None = None
    # 20/08 -- live trade flow, derived from the notification stream
    # itself (each notification IS a trade; the quote reserve rising means
    # a buy, falling means a sell). `trades_total`/`seconds_tracked` are
    # cumulative since first sight, the buy/sell pair resets per read.
    buys_since_last_read: int = 0
    sells_since_last_read: int = 0
    trades_total: int = 0
    seconds_tracked: float | None = None
    price_low_since_last_read: float | None = None
    # 19/08 -- True when this snapshot is priced from a quiet-but-connected
    # account (no notification in over max_staleness_seconds, connection
    # still live). The price itself is still trustworthy (nothing changed on
    # the account), but a caller wanting to distinguish "actively updating"
    # from "confirmed unchanged" can check this instead of re-deriving it.
    stale: bool = False


def derive_bonding_curve_address(mint: str) -> str | None:
    """The bonding-curve PDA for a mint, computed LOCALLY (no RPC call).

    20/08 -- added because the LATE-BONDING pocket sources candidates from the
    program-wide trade stream, which only ever knows MINTS: pump.fun's trade
    events carry the mint, not the curve address. Every earlier caller got the
    resolved address handed to it by PumpPortal's creation event
    (``bondingCurveKey``), so nothing had needed to derive it before -- and the
    pocket silently rejected every candidate with `blocked_progress_unknown`
    until this existed.

    Seeds ``["bonding-curve", mint]`` under the pump.fun program, straight from
    the official IDL's own instruction account list (see module docstring).
    ``None`` on anything unparseable -- never a fabricated address."""
    try:
        from solders.pubkey import Pubkey

        program = Pubkey.from_string(PUMPFUN_PROGRAM_ID)
        mint_key = Pubkey.from_string(mint)
        pda, _bump = Pubkey.find_program_address([b"bonding-curve", bytes(mint_key)], program)
        return str(pda)
    except Exception:  # noqa: BLE001 -- a bad mint is never fatal
        return None


def bonding_progress(decoded: dict | None, *, token_decimals: int | None = None) -> float | None:
    """0.0 at creation, 1.0 at graduation. ``None`` when it cannot be computed
    honestly -- never a guessed value.

    A curve already flagged ``complete`` is 1.0 by definition, whatever its
    reserves say."""
    if not decoded:
        return None
    if decoded.get("complete"):
        return 1.0
    left = decoded.get("real_token_reserves")
    if left is None:
        return None
    decimals = token_decimals if token_decimals is not None else 6
    total = INITIAL_CURVE_TOKENS * (10 ** decimals)
    if total <= 0 or left < 0 or left > total:
        return None  # inconsistent with the provisional constant -- say so
    return round(1.0 - (left / total), 4)


def decode_bonding_curve_account(raw: bytes) -> dict | None:
    """Returns the fields this module needs from a raw ``BondingCurve``
    account, or ``None`` if too short or the discriminator doesn't match
    (never a partial/guessed result)."""
    if len(raw) < BONDING_CURVE_ACCOUNT_MIN_LEN:
        return None
    if raw[:8] != BONDING_CURVE_DISCRIMINATOR:
        return None
    virtual_token_reserves = int.from_bytes(raw[OFF_VIRTUAL_TOKEN_RESERVES:OFF_VIRTUAL_TOKEN_RESERVES + 8], "little")
    virtual_quote_reserves = int.from_bytes(raw[OFF_VIRTUAL_QUOTE_RESERVES:OFF_VIRTUAL_QUOTE_RESERVES + 8], "little")
    real_token_reserves = int.from_bytes(raw[OFF_REAL_TOKEN_RESERVES:OFF_REAL_TOKEN_RESERVES + 8], "little")
    real_quote_reserves = int.from_bytes(raw[OFF_REAL_QUOTE_RESERVES:OFF_REAL_QUOTE_RESERVES + 8], "little")
    complete = bool(raw[OFF_COMPLETE])
    quote_mint = _pubkey_from_bytes(raw[OFF_QUOTE_MINT:OFF_QUOTE_MINT + 32])
    return {
        "virtual_token_reserves": virtual_token_reserves,
        "virtual_quote_reserves": virtual_quote_reserves,
        "real_token_reserves": real_token_reserves,
        "real_quote_reserves": real_quote_reserves,
        "complete": complete,
        "quote_mint": quote_mint,
    }


async def resolve_bonding_curves(
    http_client: httpx.AsyncClient, pool_mint_pairs: list[tuple[str, str]], *, rpc_http_url: str = RPC_HTTP_DEFAULT,
) -> dict[str, PumpFunBondingCurveAccount]:
    """Resolves + decodes + self-verifies each ``(bonding_curve_address,
    mint)`` pair in 2 batched ``getMultipleAccounts`` calls (never one call
    per pool). A pair that fails ANY step (not a real BondingCurve account,
    undecodable mint decimals) is silently excluded -- never a
    half-verified result handed to a caller."""
    if not pool_mint_pairs:
        return {}

    pool_addrs = [p for p, _ in pool_mint_pairs]
    mint_by_pool = dict(pool_mint_pairs)

    curve_raws = await _rpc_get_multiple_accounts(http_client, rpc_http_url, pool_addrs)
    await asyncio.sleep(SETUP_REQUEST_GAP_SECONDS)

    decoded: dict[str, dict] = {}
    for pool_addr, acc in zip(pool_addrs, curve_raws):
        if acc is None:
            continue
        raw = base64.b64decode(acc["data"][0])
        d = decode_bonding_curve_account(raw)
        if d is not None:
            decoded[pool_addr] = d
    if not decoded:
        return {}

    unique_mints = sorted({mint_by_pool[p] for p in decoded})
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

    result: dict[str, PumpFunBondingCurveAccount] = {}
    for pool_addr, d in decoded.items():
        mint = mint_by_pool[pool_addr]
        dec = decimals_by_mint.get(mint)
        if dec is None:
            logger.info("pumpfun_bonding_ws: missing decimals for mint %s (pool %s) -- excluded", mint, pool_addr)
            continue
        result[pool_addr] = PumpFunBondingCurveAccount(
            pool_address=pool_addr, mint=mint, quote_mint=d["quote_mint"], token_decimals=dec,
            curve=d,
        )
    return result


class PumpFunBondingWebSocketFeed:
    """Maintains ONE persistent websocket connection subscribed to the
    bonding-curve accounts of a set of pump.fun tokens, updating an
    in-memory price/reserve snapshot per token on every real
    ``accountNotification``. Auto-reconnects with exponential backoff on
    any disconnect. Never a single point of failure for a caller:
    ``get_snapshot`` returns ``available=False`` for any pool this feed
    can't currently price -- the caller's own REST-polling fallback is what
    makes this safe, not anything inside this class. Same architecture as
    ``PumpSwapWebSocketFeed`` (see that class's own docstring), differing
    only in what account layout it decodes and in needing ``mint`` alongside
    each pool address (see module docstring)."""

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

        self._curves: dict[str, PumpFunBondingCurveAccount] = {}
        self._raw_state: dict[str, dict] = {}  # pool_address -> decoded fields, refreshed on each notification
        self._updated_at: dict[str, float] = {}
        self._pending_subscribe: list[str] = []
        self._pending_unsubscribe: list[str] = []

        # 19/08 -- high/low tracked across EVERY notification since the
        # caller's last read, reset by get_snapshot() itself (see that
        # method + PumpFunBondingLiveSnapshot's own docstring).
        self._price_high_since_read: dict[str, float] = {}
        # 20/08 trade-flow counters, see _handle_account_notification.
        self._buys_since_read: dict[str, int] = {}
        self._sells_since_read: dict[str, int] = {}
        self._trades_total: dict[str, int] = {}
        self._first_seen_at: dict[str, float] = {}
        self._price_low_since_read: dict[str, float] = {}

        # 19/08 -- tracks the active accountSubscribe id for each pool, so
        # remove_pools() can send a real accountUnsubscribe rather than just
        # dropping local state (see that method's own docstring for the real
        # incident this fixes: 216 pools accumulated on one connection with
        # no way to ever shed a closed position, correlated with recurring
        # keepalive-timeout reconnects). ``self._ws`` is the currently
        # connected socket (``None`` between connections) -- unsubscribing
        # is a real network call, only possible while connected.
        self._sub_id_to_pool: dict[int, str] = {}
        self._pool_to_sub_id: dict[str, int] = {}
        self._ws = None

        self._sol_usd: float | None = None
        self._last_calibration_at: float = 0.0

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # --- pool management ------------------------------------------------

    async def add_pools(self, pool_mint_pairs: list[tuple[str, str]]) -> int:
        """``pool_mint_pairs``: list of ``(bonding_curve_address, mint)`` --
        see module docstring for why ``mint`` is required here (unlike
        ``PumpSwapWebSocketFeed.add_pools``, which only needs the pool
        address). Already-tracked pools are skipped, never re-resolved.
        Best-effort: a resolution failure never raises."""
        new_pairs = [(p, m) for p, m in pool_mint_pairs if p not in self._curves]
        if not new_pairs:
            return 0
        try:
            async with self._http_client_factory() as http_client:
                resolved = await resolve_bonding_curves(http_client, new_pairs, rpc_http_url=self._rpc_http_url)
        except Exception as exc:  # noqa: BLE001 -- resolution must never raise into the caller
            logger.info("pumpfun_bonding_ws: add_pools resolution failed (%s)", exc)
            return 0
        for pool_addr, curve in resolved.items():
            self._curves[pool_addr] = curve
            self._pending_subscribe.append(pool_addr)
        return len(resolved)

    def tracked_pools(self) -> list[str]:
        return list(self._curves.keys())

    def remove_pools(self, pool_addresses: list[str]) -> None:
        """Sheds a pool once the caller no longer needs it (e.g. its
        position just closed) -- 19/08, real incident this fixes: this feed
        had NO way to ever shed a subscription, so 216 pools accumulated on
        one connection over ~1h40 (every position ever opened, closed or
        not), correlated with recurring ``keepalive ping timeout``
        reconnects that left EVERY candidate unpriceable during each
        reconnect window (see ``_resolve_liquidity_snapshot`` in
        ``solana_fresh_launch_fast_discovery_shadow.py`` for the downstream
        incident this caused). Drops local state immediately (so
        ``get_snapshot`` stops reporting on it right away); the real
        ``accountUnsubscribe`` network call is sent best-effort on the next
        ``_read_loop`` pass while connected (queued via
        ``_pending_unsubscribe`` otherwise -- see ``_run_loop``, which
        clears any stale queue on a fresh connection since a new connection
        has no subscription to shed in the first place)."""
        for pool_addr in pool_addresses:
            self._curves.pop(pool_addr, None)
            self._raw_state.pop(pool_addr, None)
            self._updated_at.pop(pool_addr, None)
            self._price_high_since_read.pop(pool_addr, None)
            self._buys_since_read.pop(pool_addr, None)
            self._sells_since_read.pop(pool_addr, None)
            self._trades_total.pop(pool_addr, None)
            self._first_seen_at.pop(pool_addr, None)
            self._price_low_since_read.pop(pool_addr, None)
            sub_id = self._pool_to_sub_id.pop(pool_addr, None)
            if sub_id is not None:
                self._sub_id_to_pool.pop(sub_id, None)
                self._pending_unsubscribe.append(sub_id)
            elif pool_addr in self._pending_subscribe:
                self._pending_subscribe.remove(pool_addr)

    # --- lifecycle --------------------------------------------------------

    async def start(self, pool_mint_pairs: list[tuple[str, str]] | None = None) -> None:
        if pool_mint_pairs:
            await self.add_pools(pool_mint_pairs)
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

    def get_snapshot(self, pool_address: str) -> PumpFunBondingLiveSnapshot:
        curve = self._curves.get(pool_address)
        if curve is None:
            return PumpFunBondingLiveSnapshot(pool_address=pool_address, available=False, error="pool_not_tracked")

        updated_at = self._updated_at.get(pool_address)
        if updated_at is None:
            return PumpFunBondingLiveSnapshot(pool_address=pool_address, available=False, error="no_notification_yet")

        # 19/08 -- staleness no longer means "unusable" on its own. A quiet
        # accountSubscribe on a live connection means the account genuinely
        # hasn't changed (no new trade on this pool) -- that IS the price,
        # not missing data, and forcing every caller into a REST fallback to
        # "confirm" an unchanged price was the real cause of two real
        # incidents the same day: (1) both fresh-launch pockets' exit-tracking
        # cycles stretching from a nominal 60s to 2-12 real minutes, entirely
        # spent on REST calls re-fetching prices that hadn't moved, most of
        # them for dead/illiquid tokens sitting untouched for 50-95 real
        # minutes (verified live via getSignaturesForAddress); (2) the shared
        # REST throttle (dexpaprika.py/geckoterminal.py, one process-wide
        # lock) saturating for BOTH pockets at once. Staleness still means
        # something real: it's the signal that distinguishes it from a dead
        # WEBSOCKET CONNECTION, where the same silence could just mean we
        # stopped receiving anything at all -- checked via ``self._ws``
        # (``None`` between connections, see ``remove_pools``'s own comment).
        # Only fall back to REST-required unavailability while genuinely
        # disconnected; a stale-but-connected read still prices normally
        # below, just carries ``stale=True`` for the caller to see.
        is_stale = (time.time() - updated_at) > self._max_staleness_seconds
        if is_stale and self._ws is None:
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="stale_disconnected",
            )

        if curve.quote_mint != NATIVE_SOL_QUOTE_MARKER:
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at,
                error="non_native_sol_quote_unsupported",
            )

        state = self._raw_state.get(pool_address)
        if state is None:
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, error="reserves_not_seen_yet",
            )

        if state["complete"]:
            # Migrated -- this feed's virtual-reserve snapshot is now stale
            # by design (liquidity moved to the AMM). Caller's next stop is
            # pumpswap_ws.py, never a guessed price from here.
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, complete=True,
                error="bonding_curve_complete_use_pumpswap_ws",
            )

        if self._sol_usd is None:
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, complete=False,
                error="no_sol_usd_calibration",
            )

        virtual_token = state["virtual_token_reserves"]
        virtual_quote = state["virtual_quote_reserves"]
        if virtual_token <= 0:
            return PumpFunBondingLiveSnapshot(
                pool_address=pool_address, available=False, updated_at=updated_at, complete=False,
                error="zero_virtual_token_reserve",
            )

        # Both sides normalized to their real decimals before ratio -- see
        # module docstring's Price formula section.
        virtual_token_norm = virtual_token / (10 ** curve.token_decimals)
        virtual_quote_norm = virtual_quote / (10 ** SOL_DECIMALS)
        price_sol_per_token = virtual_quote_norm / virtual_token_norm
        price_usd = price_sol_per_token * self._sol_usd

        real_quote_norm = state["real_quote_reserves"] / (10 ** SOL_DECIMALS)
        # Same "depth = 2x one side" approximation already documented in
        # pumpswap_ws.py/solana_pump_shadow.py -- real_quote_reserves is the
        # actual SOL depositors put in (never the virtual offset).
        reserve_usd = 2.0 * real_quote_norm * self._sol_usd

        # High/low since the caller's last read (see PumpFunBondingLiveSnapshot's
        # own docstring) -- fall back to the current price if nothing was
        # tracked yet (e.g. first read right after add_pools, before any
        # notification landed while calibrated). Reset AFTER reading so the
        # next window starts fresh from here, never double-counted.
        price_high = self._price_high_since_read.get(pool_address, price_usd)
        price_low = self._price_low_since_read.get(pool_address, price_usd)
        self._price_high_since_read[pool_address] = price_usd
        self._price_low_since_read[pool_address] = price_usd

        return PumpFunBondingLiveSnapshot(
            pool_address=pool_address, price_usd=price_usd, reserve_usd=reserve_usd,
            dex_id="pumpfun", complete=False, updated_at=updated_at, available=True, stale=is_stale,
            price_high_since_last_read=max(price_high, price_usd),
            price_low_since_last_read=min(price_low, price_usd),
            # Buy/sell counters RESET on read (same window semantics as the
            # high/low above), totals stay cumulative so a caller can compute
            # velocity over the whole tracking window rather than one cycle.
            buys_since_last_read=self._buys_since_read.pop(pool_address, 0),
            sells_since_last_read=self._sells_since_read.pop(pool_address, 0),
            trades_total=self._trades_total.get(pool_address, 0),
            seconds_tracked=(
                time.time() - self._first_seen_at[pool_address]
                if pool_address in self._first_seen_at else None
            ),
        )

    # --- background loop --------------------------------------------------

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._rpc_ws_url)
        import websockets

        # ping_timeout raised 20->40s (19/08 empirical test): recurring "keepalive
        # ping timeout" reconnects observed roughly every 40-90s even after fixing
        # the unbounded-subscription leak (remove_pools) -- the event loop is likely
        # too busy (REST calls, JSON parsing of high-volume updates) to reply to a
        # pong within 20s. Widening the window tests whether these are false
        # positives from transient event-loop load rather than a real dead peer.
        return websockets.connect(self._rpc_ws_url, ping_interval=20, ping_timeout=40)

    async def _subscribe_and_confirm(self, ws, pool_addresses: list[str]) -> dict[int, str]:
        if not pool_addresses:
            return {}
        local_id_to_pool: dict[int, str] = {}
        base_id = int(time.time() * 1000) % 1_000_000_000
        for i, pool_addr in enumerate(pool_addresses):
            local_id = base_id + i
            local_id_to_pool[local_id] = pool_addr

        # 20/08 -- batched concurrent sends, see _SUBSCRIBE_BATCH_SIZE's own
        # docstring for the real incident this replaces (one-at-a-time sends
        # with a blocking gap took 133s for 333 pools, well past
        # ping_timeout). Each batch's sends fire concurrently (asyncio.gather),
        # only the GAP between batches is paced.
        items = list(local_id_to_pool.items())
        for batch_start in range(0, len(items), _SUBSCRIBE_BATCH_SIZE):
            batch = items[batch_start:batch_start + _SUBSCRIBE_BATCH_SIZE]
            await asyncio.gather(*(
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": local_id, "method": "accountSubscribe",
                    "params": [pool_addr, {"encoding": "base64", "commitment": "confirmed"}],
                }))
                for local_id, pool_addr in batch
            ))
            if batch_start + _SUBSCRIBE_BATCH_SIZE < len(items):
                await asyncio.sleep(_SUBSCRIBE_BATCH_GAP_SECONDS)

        confirmed: dict[int, str] = {}
        deadline = time.time() + _SUBSCRIBE_CONFIRM_TIMEOUT_SECONDS
        pending = set(local_id_to_pool)
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
                    confirmed[msg["result"]] = local_id_to_pool[msg["id"]]
                else:
                    logger.info(
                        "pumpfun_bonding_ws: subscribe failed for %s (%s)",
                        local_id_to_pool[msg["id"]], msg.get("error"),
                    )
        for local_id in pending:
            logger.info(
                "pumpfun_bonding_ws: no subscribe confirmation before deadline for %s",
                local_id_to_pool[local_id],
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
            logger.info("pumpfun_bonding_ws: SOL/USD calibration failed (%s)", exc)
        finally:
            self._last_calibration_at = now

    def _price_usd_for(self, pool_address: str, state: dict) -> float | None:
        """Shared by ``_apply_notification`` (to track high/low across
        notifications) and ``get_snapshot`` (to report the current price) --
        never duplicated. Returns ``None`` for anything not currently
        priceable (no calibration yet, wrong quote asset, migrated, zero
        reserve) -- same conditions ``get_snapshot`` itself checks."""
        curve = self._curves.get(pool_address)
        if curve is None or curve.quote_mint != NATIVE_SOL_QUOTE_MARKER:
            return None
        if state["complete"] or self._sol_usd is None:
            return None
        virtual_token = state["virtual_token_reserves"]
        virtual_quote = state["virtual_quote_reserves"]
        if virtual_token <= 0:
            return None
        virtual_token_norm = virtual_token / (10 ** curve.token_decimals)
        virtual_quote_norm = virtual_quote / (10 ** SOL_DECIMALS)
        return (virtual_quote_norm / virtual_token_norm) * self._sol_usd

    def _apply_notification(self, msg: dict, sub_id_to_pool: dict[int, str]) -> None:
        params = msg.get("params")
        if not params:
            return
        sub_id = params.get("subscription")
        pool_addr = sub_id_to_pool.get(sub_id)
        if pool_addr is None:
            return
        value = params.get("result", {}).get("value")
        if not value or not value.get("data"):
            return
        raw = base64.b64decode(value["data"][0])
        decoded = decode_bonding_curve_account(raw)
        if decoded is None:
            return

        # 20/08 -- TRADE-FLOW COUNTERS. Every accountNotification on a bonding
        # curve IS a trade: the account only changes when someone buys or
        # sells. The DIRECTION is readable too -- the quote reserve (SOL put
        # in) rises on a buy and falls on a sell. So the buy/sell split and the
        # trade velocity come free from a subscription that already exists,
        # with no extra call and no new provider.
        #
        # Why this matters, measured the same day: comparing x2+ winners to
        # losers on entry features, the ONE discriminating variable was how
        # much supply had already been bought (63.9% still unsold for winners
        # vs 82.8% for losers). A token that explodes is one people were
        # already buying. But that was only readable via RugCheck's async
        # backfill, i.e. AFTER entry. These counters make the same signal
        # readable DURING the pre-entry tracking window, live.
        #
        # PumpPortal's own `subscribeTokenTrade` was checked first and rejected:
        # it is a METERED endpoint (0.01 SOL / 10k events) and returned zero
        # events on 18 real fresh tokens over 30s in a live test.
        previous = self._raw_state.get(pool_addr)
        if previous is not None:
            delta_quote = decoded["virtual_quote_reserves"] - previous["virtual_quote_reserves"]
            if delta_quote > 0:
                self._buys_since_read[pool_addr] = self._buys_since_read.get(pool_addr, 0) + 1
            elif delta_quote < 0:
                self._sells_since_read[pool_addr] = self._sells_since_read.get(pool_addr, 0) + 1
        self._trades_total[pool_addr] = self._trades_total.get(pool_addr, 0) + 1
        self._first_seen_at.setdefault(pool_addr, time.time())

        self._raw_state[pool_addr] = decoded
        self._updated_at[pool_addr] = time.time()

        price = self._price_usd_for(pool_addr, decoded)
        if price is not None:
            prev_high = self._price_high_since_read.get(pool_addr)
            prev_low = self._price_low_since_read.get(pool_addr)
            self._price_high_since_read[pool_addr] = price if prev_high is None else max(prev_high, price)
            self._price_low_since_read[pool_addr] = price if prev_low is None else min(prev_low, price)

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
                logger.info("pumpfun_bonding_ws: accountUnsubscribe send failed for sub_id %s (%s)", sub_id, exc)

    async def _read_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            if self._pending_subscribe:
                newly = self._pending_subscribe
                self._pending_subscribe = []
                new_confirmed = await self._subscribe_and_confirm(ws, newly)
                self._sub_id_to_pool.update(new_confirmed)
                for sub_id, pool_addr in new_confirmed.items():
                    self._pool_to_sub_id[pool_addr] = sub_id

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
            self._apply_notification(msg, self._sub_id_to_pool)

    async def _run_loop(self) -> None:
        backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
        while not self._stop_event.is_set():
            try:
                async with self._connect() as ws:
                    self._ws = ws
                    await self._maybe_refresh_calibration()
                    pool_addresses = list(self._curves.keys())
                    self._pending_subscribe = [
                        p for p in self._pending_subscribe if p not in pool_addresses
                    ]  # avoid a double-subscribe of pools already covered by the fresh full set
                    self._pending_unsubscribe = []  # a fresh connection has no stale subscription to shed
                    confirmed = await self._subscribe_and_confirm(ws, pool_addresses)
                    self._sub_id_to_pool = dict(confirmed)
                    self._pool_to_sub_id = {p: sid for sid, p in confirmed.items()}
                    backoff = _RECONNECT_BACKOFF_INITIAL_SECONDS
                    await self._read_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a single connection error must never kill the feed
                logger.info("pumpfun_bonding_ws: feed loop error (%s) -- reconnecting in %.1fs", exc, backoff)
            finally:
                self._ws = None
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX_SECONDS)
