"""BTC signal for third-party trading agents (seam #60, Arena Virtuals/Shekel).

Exposes ARIA's REAL, already-existing BTC analyses (halving macro cycle,
RSI) in a compact form consumable by a public, read-only HTTP endpoint
(Shekel's "Custom Data Endpoint" contract: GET, JSON, no auth). Recomputes
nothing: reuses `btc_cycles` (1h cache) and the Wilder RSI already
wired for the `/vc` report (`entry_signals.rsi_series`), never a duplicate
external client.

Facts-only, honest degradation: a missing field (RSI if history is
too short, cycle if CoinGecko is unavailable) is omitted (``None``), never
replaced by an invented value — same doctrine as `btc_cycles`/`entry_signals`.

Constraint discovered on 09/07 (tested live against the real API, error_code
10012): CoinGecko's free tier refuses any request for data older than
365 days, regardless of the requested window size (not a pagination
issue). The RSI (which only needs a few weeks) therefore uses a dedicated
RECENT window (``_RSI_WINDOW_DAYS``) via CoinGecko, while
the macro cycle (`btc_cycles`, 10 years) is passed to Blockchain.com
(`services/blockchain_info.py`) — two DIFFERENT clients, two different
interfaces, never interchangeable (hence the two separate parameters
below rather than a single shared `client`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aria_core.skills.btc_cycles import BTC_COIN_ID, fetch_current_macro_phase
from aria_core.skills.entry_signals import rsi_series

NOTE = (
    "Deterministic ARIA signals only (no LLM guess). Missing fields mean the "
    "underlying data was unavailable right now, not zero. RSI is computed from "
    "real daily BTC/USD closes (CoinGecko, last ~90 days — CoinGecko's free "
    "tier hard-caps historical queries at 365 days); the BTC halving-cycle "
    "phase is computed from Blockchain.com's long history instead. No OHLC "
    "candle source is wired for BTC yet, so Fibonacci/golden-pocket/divergence "
    "signals are not included here (would require fabricating candles from "
    "close-only data)."
)

_RSI_WINDOW_DAYS = 90  # wide margin above the RSI-14 minimum (~15d), stays << 365d


async def _fetch_recent_btc_closes(*, client=None) -> list[float] | None:
    """RECENT window (≤365d, free CoinGecko constraint) for the RSI —
    distinct from the full 10-year history used by `btc_cycles`."""
    if client is None:
        from aria_core.services.coingecko import coingecko_client as client

    now = datetime.now(timezone.utc)
    start_ts = int((now - timedelta(days=_RSI_WINDOW_DAYS)).timestamp())
    end_ts = int(now.timestamp())
    result = await client.get_market_chart_range(BTC_COIN_ID, start_ts, end_ts)
    if not result.available or not result.prices:
        return None
    return [p for _, p in sorted(result.prices, key=lambda x: x[0])]


async def fetch_btc_arena_signal(*, cycle_client=None, rsi_client=None) -> dict:
    """Compact entry point for the public `/api/aria/arena-signal/btc` endpoint.

    ``cycle_client`` (Blockchain.com, long history) and ``rsi_client``
    (CoinGecko, short window) are two DISTINCT clients — never conflate
    them, their interfaces aren't interchangeable.
    """
    phase = await fetch_current_macro_phase(client=cycle_client)

    closes = await _fetch_recent_btc_closes(client=rsi_client)
    rsi_14: float | None = None
    if closes:
        series = rsi_series(closes)
        for value in reversed(series):
            if value is not None:
                rsi_14 = round(value, 1)
                break

    return {
        "btc_cycle_phase": phase.get("label") if phase else None,
        "btc_cycle_change_pct": phase.get("change_pct") if phase else None,
        "btc_cycle_since": phase.get("since") if phase else None,
        "btc_rsi_14": rsi_14,
        "note": NOTE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
