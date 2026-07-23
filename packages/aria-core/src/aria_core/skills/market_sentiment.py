"""Continuous market sentiment (facts-only, deterministic) — aligns the
vocabulary of the "Wall St Cheat Sheet — Psychology of a Market Cycle"
(reference image shared by the operator on 07/10) with REAL indicators (RSI,
position within the Bollinger Bands, momentum, retracement from the recent
high/low) rather than with a feeling.

Complements `btc_cycles.py` (halving cycle, MULTI-YEAR horizon) with a
SHORT/MEDIUM-TERM reading — the two frameworks coexist, they don't replace
each other. Like `btc_cycles`, this is a widespread analytical LENS (never a
proven market law): SIMPLE and DECLARED thresholds below.

Assumed and honest simplification: the cheat sheet's 13 emotions are NOT all
reliably distinguishable from indicators alone (no numeric signature
separates "anger" from "depression," for instance). This module groups them
into 6 defensible regimes + a neutral fallback, each with the real numbers
that produced it — never a label without evidence.

Operates on series of CLOSES (not real OHLC candles): the principal pairs
(BTC, ETH) are fed by `CoinGeckoClient.get_market_chart_range`
(`market_chart` only provides closing prices, never open/high/low) —
candlestick patterns (`candlestick_patterns.py`) therefore don't apply here,
reserved for Base tokens with real OHLC via `services/ohlcv.py`.

"No expiration" (operator request from 07/10): this module NEVER caches a
stale reading behind a TTL — every heartbeat cycle recomputes and overwrites
the last known reading (`upsert_reading`). Real freshness depends solely on
the running heartbeat, never on a silently outdated cache.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.skills.indicators import bollinger_bands
from aria_core.skills.entry_signals import rsi_series

logger = logging.getLogger(__name__)


def market_sentiment_enabled() -> bool:
    """Seam gated OFF by default. The continuous-scan heartbeat cycle only
    runs once this flag is enabled by the operator."""
    return os.environ.get("ARIA_MARKET_SENTIMENT_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

DB_PATH = str(aria_db_path())

# Principal pairs tracked continuously. STARTING list, extensible — no claim
# of exhaustiveness ("all principal pairs" in the broad sense remains a goal,
# this one covers the two most universally tracked majors (BTC = crypto macro
# reference, ETH = base layer of ARIA's Base ecosystem).
PRINCIPAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("BTC", "bitcoin"),
    ("ETH", "ethereum"),
)

_FETCH_WINDOW_DAYS = 180  # large margin under the free CoinGecko limit (365d)
_MIN_CLOSES_REQUIRED = 60  # below this threshold, reading judged unreliable (RSI/BB/momentum/trend warm-up)

_MOMENTUM_LOOKBACK = 14
_TREND_PERIOD = 50
_BOLLINGER_PERIOD = 20

# SIMPLE and DECLARED thresholds (no universal definition exists):
_RSI_EUPHORIA = 75.0
_RSI_OVERSOLD = 30.0
_RSI_NEUTRAL_LOW = 40.0
_RSI_NEUTRAL_HIGH = 70.0
_BB_UPPER_EXTREME = 1.0  # close au-dessus (ou à) la bande haute
_DECEL_MARGIN_PP = 5.0   # points de %, marge pour juger un momentum "en ralentissement"
_DRAWDOWN_CAPITULATION_PCT = -35.0
_DRAWDOWN_TOPPING_LOW_PCT = -20.0
_DRAWDOWN_TOPPING_HIGH_PCT = -2.0
_RALLY_EARLY_MAX_PCT = 20.0

REGIME_LABELS = {
    "euphorie": "Euphorie / surachat extrême (regroupe thrill + euphoria du cheat sheet)",
    "complaisance": "Complaisance / sommet (complacency — euphorie qui ralentit, signal d'alerte)",
    "anxiete_distribution": "Anxiété / distribution (anxiety + denial — la hausse se fissure)",
    "capitulation_peur": "Capitulation / peur (panic + capitulation + anger + depression)",
    "doute_accumulation": "Doute / accumulation (disbelief + hope — reprise depuis un creux)",
    "optimisme_conviction": "Optimisme / conviction haussière (optimism + belief)",
    "neutre": "Neutre (aucun régime tranché ne ressort des chiffres actuels)",
    "donnees_insuffisantes": "Données insuffisantes pour une lecture fiable",
}

# 07/20 -- dynamic Regime Switch (Gemini cross-review, explicit operator
# go-ahead on the Fear liquidity figure "200k but keep an eye on it"): 3
# meta-states that drive the momentum pipeline's hard thresholds
# (liquidity/sizing/exit discipline -- momentum_entry.py/risk_guard.py/
# paper_trader.py), derived from the 6 regimes above (deterministic, zero
# LLM, already in prod -- exactly the "objective, without the LLM inventing a
# trend" indicator requested). ``complaisance`` classified Neutral (NOT
# Euphoria) -- its own label says so: "cooling euphoria, warning signal" --
# activating it in Euphoria would arm the most aggressive parameters right
# when the market starts turning (Gemini correction, verified against the
# real code before being accepted).
META_REGIME_FEAR = "peur"
META_REGIME_NEUTRAL = "neutre"
META_REGIME_EUPHORIA = "euphorie"

_META_REGIME_MAP: dict[str, str] = {
    "euphorie": META_REGIME_EUPHORIA,
    "optimisme_conviction": META_REGIME_EUPHORIA,
    "complaisance": META_REGIME_NEUTRAL,
    "doute_accumulation": META_REGIME_NEUTRAL,
    "neutre": META_REGIME_NEUTRAL,
    "capitulation_peur": META_REGIME_FEAR,
    "anxiete_distribution": META_REGIME_FEAR,
}

_META_REGIME_RANK = {META_REGIME_FEAR: 0, META_REGIME_NEUTRAL: 1, META_REGIME_EUPHORIA: 2}


def meta_regime_rank(regime: str | None) -> int:
    """Ordinal rank (Fear < Neutral < Euphoria) -- an unknown/absent regime
    counts as Neutral (unchanged default behavior, never a more extreme invented rank)."""
    return _META_REGIME_RANK.get(regime or META_REGIME_NEUTRAL, _META_REGIME_RANK[META_REGIME_NEUTRAL])


def more_cautious_meta_regime(a: str | None, b: str | None) -> str:
    """The more cautious of the two meta-regimes (lowest rank) -- the
    foundation of the "never relax" ratchet for an already-open position (see
    paper_trader.py): once a more cautious regime has been observed (at
    entry OR during the holding period), exit discipline never becomes more
    permissive again.

    Normalizes ``None`` to Neutral BEFORE choosing -- ``meta_regime_rank(None)``
    already equals Neutral's rank, but without this normalization the
    comparison could return the original ``None`` as-is (rank equal to or
    below Neutral/Euphoria) instead of the "neutre" string -- always one of
    the 3 valid values on output, never ``None``."""
    a_norm = a or META_REGIME_NEUTRAL
    b_norm = b or META_REGIME_NEUTRAL
    return a_norm if meta_regime_rank(a_norm) <= meta_regime_rank(b_norm) else b_norm


async def resolve_meta_regime() -> str:
    """Combines the BTC/ETH readings (``latest_readings()``, pure local DB
    read, ZERO network call -- the heartbeat refreshes separately, same
    property as ``_sentiment_lines()`` already used by ``momentum_entry.py``)
    into A SINGLE meta-regime, with a deliberately ASYMMETRIC bias ("quick to
    fear, slow to greed"):
    - if EVEN ONE pair reads Fear -> Fear meta-regime (a single asset in
      capitulation is already a broad stress signal on a highly correlated
      crypto market -- the CAUTIOUS direction costs nothing to trigger early);
    - BOTH pairs (BTC AND ETH) must read Euphoria -> Euphoria meta-regime
      (relaxing hard guardrails is the RISKY direction, an isolated signal
      never suffices alone to justify it);
    - otherwise (including: no usable reading -- gate OFF, no data yet,
      ``donnees_insuffisantes`` everywhere) -> Neutral, the DEFAULT behavior
      already in place before this work -- never an invented Fear/Euphoria
      for lack of a signal, this function then degrades to a complete no-op
      for any caller."""
    readings = await latest_readings()
    metas = [
        _META_REGIME_MAP.get(r.get("regime") or "")
        for r in readings
        if r.get("regime") and r.get("regime") != "donnees_insuffisantes"
    ]
    metas = [m for m in metas if m]
    if any(m == META_REGIME_FEAR for m in metas):
        return META_REGIME_FEAR
    if len(metas) >= 2 and all(m == META_REGIME_EUPHORIA for m in metas):
        return META_REGIME_EUPHORIA
    return META_REGIME_NEUTRAL


@dataclass(frozen=True)
class SentimentReading:
    pair: str
    regime: str
    detail: str
    rsi: float | None
    bollinger_position: float | None
    momentum_pct: float | None
    drawdown_from_high_pct: float | None
    rally_from_low_pct: float | None
    trend_up: bool | None


def _sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def classify_sentiment(closes: list[float], *, pair: str = "") -> SentimentReading:
    """PURE function: same closes -> same reading. No invented value."""
    if len(closes) < _MIN_CLOSES_REQUIRED:
        return SentimentReading(
            pair=pair, regime="donnees_insuffisantes",
            detail=f"{len(closes)}/{_MIN_CLOSES_REQUIRED} closes disponibles",
            rsi=None, bollinger_position=None, momentum_pct=None,
            drawdown_from_high_pct=None, rally_from_low_pct=None, trend_up=None,
        )

    rsi_full = rsi_series(closes)
    rsi = next((v for v in reversed(rsi_full) if v is not None), None)

    period = min(_BOLLINGER_PERIOD, len(closes) - 1)
    _, upper, lower = bollinger_bands(closes, period=period)
    close = closes[-1]
    bb_pos: float | None = None
    if upper[-1] is not None and lower[-1] is not None and upper[-1] > lower[-1]:
        bb_pos = (close - lower[-1]) / (upper[-1] - lower[-1])

    lookback = min(_MOMENTUM_LOOKBACK, len(closes) - 1)
    momentum_pct = (close / closes[-1 - lookback] - 1.0) * 100.0 if closes[-1 - lookback] else None

    momentum_prev_pct = None
    if len(closes) >= 2 * lookback + 1:
        prev_close = closes[-1 - lookback]
        prev_prev_close = closes[-1 - 2 * lookback]
        if prev_prev_close:
            momentum_prev_pct = (prev_close / prev_prev_close - 1.0) * 100.0

    # High/low over the ENTIRE fetched window (up to _FETCH_WINDOW_DAYS) —
    # measures the distance to the most significant high/low of the last ~6
    # months, not just the last month (too short a window to judge a cycle top).
    recent_high = max(closes)
    recent_low = min(closes)
    drawdown_from_high_pct = (close / recent_high - 1.0) * 100.0 if recent_high else None
    rally_from_low_pct = (close / recent_low - 1.0) * 100.0 if recent_low else None

    trend_sma = _sma(closes, min(_TREND_PERIOD, len(closes) - 1))
    trend_up = close > trend_sma if trend_sma is not None else None

    if rsi is None or bb_pos is None:
        return SentimentReading(
            pair=pair, regime="donnees_insuffisantes",
            detail="RSI ou Bollinger indisponible malgré assez de closes (série trop plate ?)",
            rsi=rsi, bollinger_position=bb_pos, momentum_pct=momentum_pct,
            drawdown_from_high_pct=drawdown_from_high_pct, rally_from_low_pct=rally_from_low_pct,
            trend_up=trend_up,
        )

    decelerating = (
        momentum_prev_pct is not None
        and momentum_pct is not None
        and momentum_pct < momentum_prev_pct - _DECEL_MARGIN_PP
    )

    regime: str
    detail: str
    if rsi >= _RSI_EUPHORIA and bb_pos >= _BB_UPPER_EXTREME:
        if decelerating:
            regime = "complaisance"
            detail = f"RSI {rsi:.0f} extrême mais momentum en ralentissement ({momentum_pct:+.1f}% vs {momentum_prev_pct:+.1f}% avant)"
        else:
            regime = "euphorie"
            detail = f"RSI {rsi:.0f}, prix au-dessus de la bande de Bollinger haute (position {bb_pos:.2f})"
    elif drawdown_from_high_pct is not None and drawdown_from_high_pct <= _DRAWDOWN_CAPITULATION_PCT and rsi <= _RSI_OVERSOLD:
        regime = "capitulation_peur"
        detail = f"retracement {drawdown_from_high_pct:+.1f}% depuis le plus haut récent, RSI {rsi:.0f} survendu"
    elif (
        trend_up is False
        and drawdown_from_high_pct is not None
        and _DRAWDOWN_TOPPING_LOW_PCT <= drawdown_from_high_pct <= _DRAWDOWN_TOPPING_HIGH_PCT
        and _RSI_NEUTRAL_LOW <= rsi <= _RSI_NEUTRAL_HIGH
    ):
        regime = "anxiete_distribution"
        detail = f"tendance cassée, retracement {drawdown_from_high_pct:+.1f}% depuis le plus haut récent, RSI {rsi:.0f}"
    elif (
        trend_up is False
        and rally_from_low_pct is not None
        and 0 <= rally_from_low_pct <= _RALLY_EARLY_MAX_PCT
        and rsi <= _RSI_NEUTRAL_HIGH
    ):
        regime = "doute_accumulation"
        detail = f"reprise {rally_from_low_pct:+.1f}% depuis le plus bas récent, tendance pas encore confirmée, RSI {rsi:.0f}"
    elif trend_up is True and rsi >= _RSI_NEUTRAL_LOW and momentum_pct is not None and momentum_pct > 0:
        # No upper cap on RSI here: a very high RSI WITHOUT breaking out of
        # the Bollinger band (a case already handled by the euphoria/
        # complacency branch above) remains a strong uptrend, not an extreme signal.
        regime = "optimisme_conviction"
        detail = f"tendance haussière confirmée, RSI {rsi:.0f}, momentum {momentum_pct:+.1f}% sur {lookback} bougies"
    else:
        regime = "neutre"
        detail = f"RSI {rsi:.0f}, position Bollinger {bb_pos:.2f} — aucun régime tranché"

    return SentimentReading(
        pair=pair, regime=regime, detail=detail, rsi=round(rsi, 1),
        bollinger_position=round(bb_pos, 3),
        momentum_pct=round(momentum_pct, 1) if momentum_pct is not None else None,
        drawdown_from_high_pct=round(drawdown_from_high_pct, 1) if drawdown_from_high_pct is not None else None,
        rally_from_low_pct=round(rally_from_low_pct, 1) if rally_from_low_pct is not None else None,
        trend_up=trend_up,
    )


async def _fetch_recent_closes(coin_id: str, *, client=None, days: int = _FETCH_WINDOW_DAYS) -> list[float] | None:
    if client is None:
        from aria_core.services.coingecko import coingecko_client as client

    now = datetime.now(timezone.utc)
    start_ts = int((now - timedelta(days=days)).timestamp())
    end_ts = int(now.timestamp())
    result = await client.get_market_chart_range(coin_id, start_ts, end_ts)
    if not result.available or not result.prices:
        return None
    return [p for _, p in sorted(result.prices, key=lambda x: x[0])]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_sentiment (
                pair TEXT PRIMARY KEY,
                regime TEXT NOT NULL,
                detail TEXT NOT NULL,
                rsi REAL,
                bollinger_position REAL,
                momentum_pct REAL,
                drawdown_from_high_pct REAL,
                rally_from_low_pct REAL,
                trend_up INTEGER,
                computed_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def upsert_reading(reading: SentimentReading) -> None:
    """ALWAYS overwrites this pair's previous reading — no expiration to check
    on read, freshness depends solely on the last heartbeat cycle that
    succeeded in writing here."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO market_sentiment
                (pair, regime, detail, rsi, bollinger_position, momentum_pct,
                 drawdown_from_high_pct, rally_from_low_pct, trend_up, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair) DO UPDATE SET
                regime=excluded.regime, detail=excluded.detail, rsi=excluded.rsi,
                bollinger_position=excluded.bollinger_position, momentum_pct=excluded.momentum_pct,
                drawdown_from_high_pct=excluded.drawdown_from_high_pct,
                rally_from_low_pct=excluded.rally_from_low_pct, trend_up=excluded.trend_up,
                computed_at=excluded.computed_at
            """,
            (
                reading.pair, reading.regime, reading.detail, reading.rsi,
                reading.bollinger_position, reading.momentum_pct,
                reading.drawdown_from_high_pct, reading.rally_from_low_pct,
                None if reading.trend_up is None else int(reading.trend_up),
                now,
            ),
        )
        await db.commit()


async def latest_readings() -> list[dict]:
    """All latest persisted readings (one per pair), for Telegram/cockpit
    display. Recomputes nothing: the heartbeat is what refreshes it."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT pair, regime, detail, rsi, bollinger_position, momentum_pct, "
                "drawdown_from_high_pct, rally_from_low_pct, trend_up, computed_at "
                "FROM market_sentiment ORDER BY pair"
            )
        ).fetchall()
    cols = [
        "pair", "regime", "detail", "rsi", "bollinger_position", "momentum_pct",
        "drawdown_from_high_pct", "rally_from_low_pct", "trend_up", "computed_at",
    ]
    return [dict(zip(cols, row)) for row in rows]


async def run_market_sentiment_cycle() -> dict:
    """Refreshes the reading for ALL principal pairs, one by one. A pair
    whose fetch fails doesn't interrupt the others (graceful degradation,
    same doctrine as `heartbeat.py`)."""
    updated: list[str] = []
    failed: list[str] = []
    for symbol, coin_id in PRINCIPAL_PAIRS:
        try:
            closes = await _fetch_recent_closes(coin_id)
            if not closes:
                failed.append(symbol)
                continue
            reading = classify_sentiment(closes, pair=symbol)
            await upsert_reading(reading)
            updated.append(symbol)
        except Exception:
            logger.exception("market_sentiment_cycle failed for %s", symbol)
            failed.append(symbol)
    return {"updated": updated, "failed": failed}


def format_sentiment_prompt_lines(readings: list[dict]) -> list[str]:
    """Compact lines for injection into an LLM prompt (distinct from
    ``format_sentiment_report``, meant for Telegram/cockpit display) --
    extracted on 07/19 from ``vc_analysis.py`` (inline logic duplicated in
    substance) so that ``momentum_entry.py`` benefits from the SAME analysis
    depth as ``/vc`` without reimplementing the filtering/sanitization.
    Ignores ``donnees_insuffisantes`` readings (nothing usable), sanitizes
    every field (mandate #192 -- ``detail``/``pair`` ultimately come from a
    computation on real market prices, not arbitrary third-party content, but
    the same discipline applies by default)."""
    from aria_core.sanitize import sanitize_untrusted_text

    lines: list[str] = []
    for r in readings:
        regime = r.get("regime")
        if not regime or regime == "donnees_insuffisantes":
            continue
        label = sanitize_untrusted_text(REGIME_LABELS.get(regime, regime), 120)
        pair = sanitize_untrusted_text(r.get("pair"), 10)
        detail = sanitize_untrusted_text(r.get("detail"), 200)
        lines.append(f"- {pair} : {label} ({detail})")
    return lines


def format_sentiment_report(readings: list[dict]) -> str:
    if not readings:
        return "Sentiment de marché : aucune lecture encore disponible (le cycle heartbeat n'a pas encore tourné)."
    lines = ["🎭 ARIA — sentiment de marché (paires principales)", ""]
    for r in readings:
        label = REGIME_LABELS.get(r["regime"], r["regime"])
        lines.append(f"• {r['pair']} : {label}")
        lines.append(f"   {r['detail']}")
        lines.append(f"   calculé {r['computed_at']}")
    lines.append("")
    lines.append(
        "Cadre de lecture inspiré du Wall St Cheat Sheet (psychologie du cycle de "
        "marché) — un modèle répandu, simplifié en régimes mesurables, pas une loi "
        "de marché prouvée."
    )
    return "\n".join(lines)
