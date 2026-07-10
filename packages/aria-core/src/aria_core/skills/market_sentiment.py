"""Sentiment de marché continu (facts-only, déterministe) — aligne le vocabulaire
du « Wall St Cheat Sheet — Psychology of a Market Cycle » (image de référence
partagée par l'opérateur le 10/07) sur des indicateurs RÉELS (RSI, position dans
les bandes de Bollinger, momentum, retracement depuis le plus haut/bas récent)
plutôt que sur un ressenti.

Complète `btc_cycles.py` (cycle de halving, horizon PLURI-ANNUEL) par une lecture
COURT/MOYEN TERME — les deux cadres coexistent, ne se remplacent pas. Comme
`btc_cycles`, c'est une LENTE d'analyse répandue (jamais une loi de marché
prouvée) : seuils SIMPLES et DÉCLARÉS ci-dessous.

Simplification assumée et honnête : les 13 émotions du cheat sheet ne sont PAS
toutes distinguables de façon fiable depuis des indicateurs seuls (aucune
signature numérique ne sépare « colère » de « dépression », par exemple). Ce
module regroupe en 6 régimes défendables + un repli neutre, chacun avec les
chiffres réels qui l'ont produit — jamais un label sans preuve.

Opère sur des séries de CLOSES (pas de vraies bougies OHLC) : les paires
principales (BTC, ETH) sont alimentées par `CoinGeckoClient.get_market_chart_range`
(`market_chart` ne fournit que des prix de clôture, jamais l'open/high/low) —
les patterns de bougies (`candlestick_patterns.py`) ne s'appliquent donc pas ici,
réservés aux tokens Base avec OHLC réel via `services/ohlcv.py`.

« Sans expiration » (demande opérateur du 10/07) : ce module ne met JAMAIS en
cache une lecture périmée derrière un TTL — chaque cycle heartbeat recalcule et
écrase la dernière lecture connue (`upsert_reading`). La fraîcheur réelle dépend
uniquement du heartbeat qui tourne, jamais d'un cache silencieusement dépassé.
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
    """Seam gaté OFF par défaut. Le cycle heartbeat de scan continu ne tourne
    qu'une fois ce flag activé par l'opérateur."""
    return os.environ.get("ARIA_MARKET_SENTIMENT_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

DB_PATH = str(aria_db_path())

# Paires principales suivies en continu. Liste de DÉPART, extensible — pas une
# prétention d'exhaustivité ("toutes les paires principales" au sens large reste
# un objectif, celui-ci couvre les deux majors les plus universellement suivis
# (BTC = référence macro crypto, ETH = base layer de l'écosystème Base d'ARIA).
PRINCIPAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("BTC", "bitcoin"),
    ("ETH", "ethereum"),
)

_FETCH_WINDOW_DAYS = 180  # large marge sous la limite gratuite CoinGecko (365j)
_MIN_CLOSES_REQUIRED = 60  # sous ce seuil, lecture jugée non fiable (chauffe RSI/BB/momentum/tendance)

_MOMENTUM_LOOKBACK = 14
_TREND_PERIOD = 50
_BOLLINGER_PERIOD = 20

# Seuils SIMPLES et DÉCLARÉS (aucune définition universelle n'existe) :
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
    """Fonction PURE : mêmes closes -> même lecture. Aucune valeur inventée."""
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

    # Plus haut/bas sur TOUTE la fenêtre récupérée (jusqu'à _FETCH_WINDOW_DAYS) —
    # mesure la distance au sommet/creux le plus marquant des ~6 derniers mois,
    # pas seulement du dernier mois (fenêtre trop courte pour juger un sommet de cycle).
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
        # Pas de plafond haut sur le RSI ici : un RSI très élevé SANS sortie de bande
        # de Bollinger (cas déjà traité par la branche euphorie/complaisance
        # ci-dessus) reste une tendance haussière forte, pas un signal d'extrême.
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
    """Écrase TOUJOURS la lecture précédente de cette paire — aucune expiration à
    vérifier en lecture, la fraîcheur dépend uniquement du dernier cycle heartbeat
    qui a réussi à écrire ici."""
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
    """Toutes les dernières lectures persistées (une par paire), pour affichage
    Telegram/cockpit. Ne recalcule rien : c'est le heartbeat qui rafraîchit."""
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
    """Rafraîchit la lecture de TOUTES les paires principales, une par une. Une
    paire dont le fetch échoue n'interrompt pas les autres (dégradation douce,
    même doctrine que `heartbeat.py`)."""
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
