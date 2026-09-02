"""Turn raw on-chain events into a normalized trajectory, strictly causally.

Why this shape and not candles (02/09, operator-directed): *"un graphique c'est
pour un humain avec les bougies, un bot lui pourrait juste faire une courbe
linéaire avec la forme"*. A candle is a presentation convention for the eye --
open/high/low/close, wicks, colors. A detector needs the trajectory and the
flows behind it, nothing more. Building the system on candles would drag in
granularity choices, pair-age handling, gap filling and multi-source merging,
all to serve a rendering format. The screener next door proves the cost: 69% of
its 64k candles are empty shells, and the only usable ones are precisely those
rebuilt from decoded swaps.

**Strict causality is the whole point.** ``build_trajectory(..., t_end)`` reads
ONLY events at ``block_timestamp <= t_end``. Nothing later can leak in, by
construction rather than by discipline -- which is what makes a replay honest.
The operator's rule: at T, ARIA knows ``<= T``; the future exists only to score
what was decided at T, never to compute it.

**No pattern is named here.** No RSI, no EMA, no "bottom", no score. In
particular there is deliberately no ``bottom_2``: a second bottom only exists
once the price has risen again, so detecting it at T requires knowing the
future. What IS causal is ``retest_of_prior_low`` -- the price is returning to
a low ALREADY OBSERVED -- and that is measurable at T. Naming the motif is how
a replay ends up finding the pattern it was built to find.

**Fixed window, not fixed candle count.** Every trajectory covers the same
physical duration before T and is resampled to the same number of points,
whatever the pair's age or swap count. A token with 30 swaps and one with 3,000
produce comparable series. ``coverage_ratio`` reports how much of the requested
window actually contains data, so a 37-minute-old pair is visibly partial
instead of silently padded.

**Direction is never invented.** A swap's ``amount0``/``amount1`` are signed
from the POOL's side; turning that into "buy" or "sell" of a given token
requires knowing whether the token is currency0 or currency1. When that
orientation is unknown, the series are exposed as ``flow0``/``flow1`` with
``token_side = None`` rather than guessed -- an unlabelled flow is honest, a
wrongly labelled one silently inverts every buy/sell feature downstream.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import aiosqlite

from aria_core.onchain_replay_backfill import (
    DB_PATH,
    EVENT_MODIFY_LIQUIDITY,
    EVENT_SWAP,
    POOL_TABLE,
    TABLE,
)

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 3600
DEFAULT_POINTS = 120

# The number of 32-byte words in a Swap's `data` identifies the AMM version --
# derived from the event signatures themselves, not from a per-pool config we
# would have to keep in sync:
#   v2  Swap(address,uint256,uint256,uint256,uint256,address)  -> 4 (2 indexed)
#   v3  Swap(address,address,int256,int256,uint160,uint128,int24) -> 5
#   v4  Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24) -> 6
# Measured on the real backfill: MEOW's v4 pool yields 6 words on 3162 swaps,
# the v3 pool 5 words on 7904. A shape we do not recognise is skipped and
# counted, never decoded on a guess.
_WORDS_V2, _WORDS_V3, _WORDS_V4 = 4, 5, 6

_INT256_MAX = 1 << 255
_UINT256 = 1 << 256


@dataclass
class TrajectoryPoint:
    """One resampled bucket. Counts are per-bucket, price is last-seen."""

    t: int
    price_rel: float | None = None
    swaps: int = 0
    flow0: float = 0.0
    flow1: float = 0.0
    wallets: int = 0
    liquidity_events: int = 0


@dataclass
class Trajectory:
    token: str
    chain: str
    pool_id: str
    t_end: int
    window_seconds: int
    points: list[TrajectoryPoint] = field(default_factory=list)
    events_used: int = 0
    swaps_used: int = 0
    undecodable: int = 0
    first_event_ts: int | None = None
    coverage_ratio: float = 0.0
    token_side: int | None = None          # 0 or 1 once orientation is known
    price_basis: str = "quote_per_token"   # native quote, never USD (no oracle at T)
    unique_wallets: int = 0
    # Amplitude, kept BECAUSE the shape gets normalized away (02/09, operator:
    # "que l'echelle du graphique soit toujours parfaitement net ... comme ca
    # si la paire date de 24h ou 6 mois on a toujours le meme visu"). Fixing
    # both axes is what makes two trajectories comparable -- and it also erases
    # how violent the move was, which is itself discriminating. So the shape is
    # normalized for comparison and the amplitude travels beside it, never
    # instead of it.
    price_min_rel: float | None = None
    price_max_rel: float | None = None
    drawdown_from_peak: float | None = None   # worst fall from the window's high, at T
    runup_from_low: float | None = None       # best rise from the window's low, at T
    error: str | None = None


def _word(data_hex: str, index: int) -> int:
    """The n-th 32-byte word of an event's data, as an unsigned integer."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    start = index * 64
    return int(raw[start:start + 64], 16)


def _signed(value: int) -> int:
    """Two's-complement reading. int128 and int256 share this representation
    once the word is unpacked, so one helper covers v3 and v4."""
    return value - _UINT256 if value >= _INT256_MAX else value


def decode_swap(data_hex: str) -> dict | None:
    """Amounts and sqrtPriceX96 from a raw Swap payload, or None if unknown.

    Returns amounts from the POOL's perspective, unlabelled. Assigning
    buy/sell requires the token's side, which this function does not know and
    will not assume.
    """
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    words = len(raw) // 64
    try:
        if words == _WORDS_V4:
            return {
                "version": "v4",
                "amount0": _signed(_word(data_hex, 0)),
                "amount1": _signed(_word(data_hex, 1)),
                "sqrt_price_x96": _word(data_hex, 2),
                "liquidity": _word(data_hex, 3),
            }
        if words == _WORDS_V3:
            return {
                "version": "v3",
                "amount0": _signed(_word(data_hex, 0)),
                "amount1": _signed(_word(data_hex, 1)),
                "sqrt_price_x96": _word(data_hex, 2),
                "liquidity": _word(data_hex, 3),
            }
        if words == _WORDS_V2:
            # v2 has no price in the event: amounts only, split in/out. The
            # ratio of the two non-zero legs IS the executed price, which is
            # why sqrt_price_x96 stays None here rather than being faked.
            a0_in, a1_in = _word(data_hex, 0), _word(data_hex, 1)
            a0_out, a1_out = _word(data_hex, 2), _word(data_hex, 3)
            return {
                "version": "v2",
                "amount0": a0_in - a0_out,
                "amount1": a1_in - a1_out,
                "sqrt_price_x96": None,
                "liquidity": None,
            }
    except (ValueError, IndexError):
        return None
    return None


def price_from_amounts(amount0: int, amount1: int, *, token_side: int | None) -> float | None:
    """Executed price in quote-per-token, from the swap's own amounts.

    Deliberately preferred over sqrtPriceX96 for the trajectory: the amount
    ratio is what the trade ACTUALLY got, while sqrtPriceX96 is the pool's
    post-trade marginal price. For reconstructing a traded path, the realised
    ratio is the honest number -- and it is the only one v2 exposes, so using
    it everywhere keeps the three versions comparable instead of mixing two
    definitions of "price" in one series.

    Returns None when either leg is zero (nothing to divide) or the token's
    side is unknown -- never a guessed orientation.
    """
    if token_side is None or amount0 == 0 or amount1 == 0:
        return None
    a0, a1 = abs(amount0), abs(amount1)
    return a0 / a1 if token_side == 1 else a1 / a0


async def _resolve_token_side(pool_id: str, chain: str, token: str | None) -> int | None:
    """Which currency the token is, from the discovery table -- never inferred.

    Only v4 pools populate currency0/currency1 (their Initialize event carries
    both, indexed). A v2/v3 pool would need a token0()/token1() call, which is
    a network read this function will not make silently; it returns None and
    the trajectory reports unlabelled flows.
    """
    if not token:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT currency0, currency1 FROM {POOL_TABLE} WHERE chain=? AND pool_id=?",
            (chain, pool_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    tl = token.lower()
    if (row["currency0"] or "").lower() == tl:
        return 0
    if (row["currency1"] or "").lower() == tl:
        return 1
    return None


async def build_trajectory(
    pool_id: str,
    chain: str,
    t_end: int,
    *,
    token: str | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    points: int = DEFAULT_POINTS,
) -> Trajectory:
    """The normalized trajectory over ``[t_end - window_seconds, t_end]``.

    Prices are relative to the window's FIRST observed price (that point is
    1.0), which removes absolute price, market cap and decimals in one step --
    two tokens six orders of magnitude apart become directly comparable, which
    is the entire point of comparing shapes across tokens.
    """
    traj = Trajectory(
        token=(token or "").lower(), chain=chain, pool_id=pool_id,
        t_end=t_end, window_seconds=window_seconds,
    )
    t_start = t_end - window_seconds
    if points <= 0 or window_seconds <= 0:
        traj.error = "window_seconds and points must be positive"
        return traj

    traj.token_side = await _resolve_token_side(pool_id, chain, token)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # `block_timestamp <= t_end` is the causality guarantee, enforced in
        # SQL rather than in a later filter someone could forget.
        cur = await db.execute(
            f"SELECT block_timestamp, event_type, data_hex, tx_sender "
            f"FROM {TABLE} WHERE chain=? AND pool_id=? "
            f"AND block_timestamp IS NOT NULL "
            f"AND block_timestamp > ? AND block_timestamp <= ? "
            f"ORDER BY block_timestamp ASC, log_index ASC",
            (chain, pool_id, t_start, t_end),
        )
        rows = await cur.fetchall()

    if not rows:
        traj.error = "no event in this window"
        return traj

    bucket_span = window_seconds / points
    buckets = [
        TrajectoryPoint(t=int(t_start + (i + 1) * bucket_span)) for i in range(points)
    ]
    wallets_per_bucket: list[set] = [set() for _ in range(points)]
    all_wallets: set = set()

    first_price: float | None = None
    last_price_rel: float | None = None

    for row in rows:
        ts = row["block_timestamp"]
        idx = min(points - 1, max(0, int((ts - t_start) / bucket_span)))
        b = buckets[idx]
        traj.events_used += 1
        if traj.first_event_ts is None:
            traj.first_event_ts = ts

        if row["event_type"] == EVENT_MODIFY_LIQUIDITY:
            b.liquidity_events += 1
            continue
        if row["event_type"] != EVENT_SWAP:
            continue

        decoded = decode_swap(row["data_hex"] or "")
        if decoded is None:
            traj.undecodable += 1
            continue

        traj.swaps_used += 1
        b.swaps += 1
        b.flow0 += float(decoded["amount0"])
        b.flow1 += float(decoded["amount1"])
        sender = row["tx_sender"]
        if sender:
            wallets_per_bucket[idx].add(sender)
            all_wallets.add(sender)

        price = price_from_amounts(
            decoded["amount0"], decoded["amount1"], token_side=traj.token_side
        )
        if price and price > 0:
            if first_price is None:
                first_price = price
            last_price_rel = price / first_price
        if last_price_rel is not None:
            b.price_rel = last_price_rel

    # Forward-fill the price ONLY across buckets that follow an observed one.
    # Buckets before the first trade keep price_rel=None: no trade means no
    # price, and inventing one is the "synthetic candle" defect measured next
    # door (44,120 filler rows that nothing distinguished from real data).
    carried: float | None = None
    for i, b in enumerate(buckets):
        if b.price_rel is not None:
            carried = b.price_rel
        elif carried is not None:
            b.price_rel = carried
        b.wallets = len(wallets_per_bucket[i])

    traj.points = buckets
    traj.unique_wallets = len(all_wallets)
    observed = window_seconds if traj.first_event_ts is None else (t_end - traj.first_event_ts)
    traj.coverage_ratio = round(min(1.0, max(0.0, observed / window_seconds)), 4)

    # Amplitude measured BEFORE any shape normalization, and strictly within
    # the window -- both computed from observed points only, so they stay
    # causal: the peak is the highest price ALREADY SEEN at T, never a later
    # one. `drawdown_from_peak` is where the last price sits relative to that
    # peak, which is the causal half of what a human calls "it dumped".
    observed_prices = [p.price_rel for p in buckets if p.price_rel is not None]
    if observed_prices:
        traj.price_min_rel = min(observed_prices)
        traj.price_max_rel = max(observed_prices)
        last = observed_prices[-1]
        if traj.price_max_rel and traj.price_max_rel > 0:
            traj.drawdown_from_peak = round(last / traj.price_max_rel - 1.0, 6)
        if traj.price_min_rel and traj.price_min_rel > 0:
            traj.runup_from_low = round(last / traj.price_min_rel - 1.0, 6)
    return traj


def normalized_shape(traj: Trajectory) -> list[float | None]:
    """Price rescaled to [0, 1] over the window -- the comparable shape.

    This is what makes a token that moved +500% and one that moved +20% sit on
    the same canvas: same width (fixed point count), same height (min-max),
    whatever the pair's age or absolute price. The amplitude that this erases
    is not lost -- it is carried by ``price_min_rel`` / ``price_max_rel`` /
    ``drawdown_from_peak`` / ``runup_from_low`` on the trajectory itself.

    A flat window (min == max) returns 0.5 everywhere rather than dividing by
    zero: a flat line IS the honest shape there, and 0.5 keeps it centred
    instead of pinning it to an arbitrary edge.
    """
    values = [p.price_rel for p in traj.points]
    observed = [v for v in values if v is not None]
    if not observed:
        return values
    lo, hi = min(observed), max(observed)
    span = hi - lo
    if span <= 0:
        return [None if v is None else 0.5 for v in values]
    return [None if v is None else round((v - lo) / span, 6) for v in values]


def to_series(traj: Trajectory) -> dict:
    """Column-oriented view -- the shape a feature layer or a chart consumes.

    Kept separate from the dataclass so the trajectory stays the single source
    and every downstream representation (features, human chart, export) is
    derived from it rather than from another collection pass.
    """
    return {
        "t": [p.t for p in traj.points],
        "price_rel": [p.price_rel for p in traj.points],
        # Both are exposed on purpose: `shape` is the comparable curve (same
        # canvas for every token), `price_rel` keeps the real move. Publishing
        # only the normalized one would quietly discard amplitude.
        "shape": normalized_shape(traj),
        "swaps": [p.swaps for p in traj.points],
        "flow0": [p.flow0 for p in traj.points],
        "flow1": [p.flow1 for p in traj.points],
        "wallets": [p.wallets for p in traj.points],
        "liquidity_events": [p.liquidity_events for p in traj.points],
        "meta": {
            "token": traj.token, "chain": traj.chain, "pool_id": traj.pool_id,
            "t_end": traj.t_end, "window_seconds": traj.window_seconds,
            "coverage_ratio": traj.coverage_ratio, "token_side": traj.token_side,
            "price_min_rel": traj.price_min_rel, "price_max_rel": traj.price_max_rel,
            "drawdown_from_peak": traj.drawdown_from_peak,
            "runup_from_low": traj.runup_from_low,
            "swaps_used": traj.swaps_used, "undecodable": traj.undecodable,
            "unique_wallets": traj.unique_wallets, "price_basis": traj.price_basis,
        },
    }
