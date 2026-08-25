"""Chain-level liquidity/activity regime -- the REGIME grain for
scalping/shadow (see ``services/defillama.py``'s module docstring for the
full three-grain design: this is the ONLY grain that serves scalping's real
need, "is there a capital/activity inflow RIGHT NOW", to modulate entry
FREQUENCY -- it never picks a token, that stays the pockets' own job).

Same "no expiration" pattern as ``market_sentiment.py`` (07/10 operator
request): every heartbeat cycle recomputes and overwrites the last known
reading (``upsert_reading``) -- no dupliated raw history is kept locally,
DefiLlama's own API already serves the complete series on demand for free
(verified 25/08: one call returns the full history, not just the delta).

Thresholds sourced from LlamaAI's calibration answers (25/08,
``docs/HANDOFF_DEFILLAMA.md``, "LlamaAI's answers to the 10 calibration
questions" entry) -- explicitly framed by LlamaAI itself as risk-management
judgment, not a DefiLlama-documented standard:
- 30-day EWMA baseline (beats both a noisy 7-day window and an unreliable
  90-day window given Robinhood Chain's ~17-week depth at the time).
- 60-90 day burn-in before ANY automatic blocking gate activates on a chain
  (a young chain reads ``donnees_insuffisantes``, never a guessed regime).
- healthy-vs-toxic spike distinguished by a volume+TVL coupling checked over
  a 3-7 day window (TVL holding/rising after the spike = healthy, TVL
  collapsing right after = toxic) -- never judged intraday alone, which the
  data could not support anyway (confirmed 25/08: DefiLlama's own points are
  daily, no intraday granularity exists to judge on).

The volume-ratio and TVL-drop THRESHOLDS themselves (``INFLOW_RATIO_THRESHOLD``,
``TOXIC_TVL_DROP_PCT``) are OUR judgment call, not LlamaAI's -- it explicitly
does not certify a weighting/threshold methodology. Conservative starting
point per the Ingestion doctrine (CLAUDE.md): a temporary hypothesis,
recalibrated once real regime reads accumulate against real shadow closures
(n>=100), not a validated setting."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services import defillama

logger = logging.getLogger(__name__)


def chain_liquidity_regime_enabled() -> bool:
    """Seam gated OFF by default. The heartbeat cycle only refreshes the
    table once this flag is enabled by the operator -- the pockets' own
    read (``latest_regime``) fails open (None -> no opinion) regardless, so
    leaving this OFF costs nothing beyond the exogenous signal being absent."""
    return os.environ.get("ARIA_CHAIN_LIQUIDITY_REGIME_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


DB_PATH = str(aria_db_path())

# LlamaAI 25/08 calibration (see module docstring).
EWMA_WINDOW_DAYS = 30
BURN_IN_DAYS = 60
CONFIRMATION_WINDOW_DAYS = 7

# Our own judgment call, not LlamaAI's -- see module docstring.
INFLOW_RATIO_THRESHOLD = 1.5   # today's volume >= 1.5x its 30d EWMA
TOXIC_TVL_DROP_PCT = -10.0     # TVL down >=10% over the confirmation window

REGIME_INFLOW = "afflux_sain"
REGIME_TOXIC_SPIKE = "pic_toxique"
REGIME_CALM = "calme"
REGIME_INSUFFICIENT = "donnees_insuffisantes"

# Chain slugs as DefiLlama's own two endpoints expect them (25/08 finding:
# TVL history wants the CAPITALIZED form, DEX volume wants lowercase --
# get_chain_dex_volume already lowercases internally, only this one matters).
_TVL_CHAIN_SLUG = {"base": "Base", "robinhood": "Robinhood", "solana": "Solana"}


def _ewma(values: list[float], *, span: int) -> float | None:
    """Standard EWMA (alpha = 2/(span+1)), oldest-to-newest. None if empty."""
    if not values:
        return None
    alpha = 2.0 / (span + 1)
    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma
    return ewma


@dataclass(frozen=True)
class ChainRegimeReading:
    chain: str
    regime: str
    detail: str
    volume_ratio_to_ewma: float | None
    tvl_trend_pct: float | None
    history_days: int


def classify_chain_regime(
    chain: str,
    tvl_points: list[tuple[int, float]],
    volume_points: list[tuple[int, float]],
) -> ChainRegimeReading:
    """PURE function: same points -> same reading. No invented value.

    ``tvl_points``/``volume_points`` are (unix_ts, value) daily series,
    oldest first (``services/defillama.py``'s own ordering)."""
    history_days = min(len(tvl_points), len(volume_points))
    if history_days < BURN_IN_DAYS:
        return ChainRegimeReading(
            chain=chain, regime=REGIME_INSUFFICIENT,
            detail=f"{history_days}/{BURN_IN_DAYS} jours d'historique (periode de rodage)",
            volume_ratio_to_ewma=None, tvl_trend_pct=None, history_days=history_days,
        )

    volumes = [v for _, v in volume_points]
    baseline = volumes[-(EWMA_WINDOW_DAYS + 1):-1] if len(volumes) > EWMA_WINDOW_DAYS else volumes[:-1]
    ewma_volume = _ewma(baseline, span=EWMA_WINDOW_DAYS)
    latest_volume = volumes[-1]
    if not ewma_volume:
        return ChainRegimeReading(
            chain=chain, regime=REGIME_INSUFFICIENT,
            detail="EWMA volume non calculable (serie plate ou vide)",
            volume_ratio_to_ewma=None, tvl_trend_pct=None, history_days=history_days,
        )
    volume_ratio = latest_volume / ewma_volume

    tvl_values = [v for _, v in tvl_points]
    window = min(CONFIRMATION_WINDOW_DAYS, len(tvl_values) - 1)
    tvl_then = tvl_values[-1 - window]
    tvl_now = tvl_values[-1]
    tvl_trend_pct = ((tvl_now / tvl_then) - 1.0) * 100.0 if tvl_then else None

    if volume_ratio < INFLOW_RATIO_THRESHOLD:
        return ChainRegimeReading(
            chain=chain, regime=REGIME_CALM,
            detail=f"volume {volume_ratio:.2f}x son EWMA {EWMA_WINDOW_DAYS}j -- pas d'afflux notable",
            volume_ratio_to_ewma=round(volume_ratio, 3),
            tvl_trend_pct=round(tvl_trend_pct, 1) if tvl_trend_pct is not None else None,
            history_days=history_days,
        )

    if tvl_trend_pct is not None and tvl_trend_pct <= TOXIC_TVL_DROP_PCT:
        return ChainRegimeReading(
            chain=chain, regime=REGIME_TOXIC_SPIKE,
            detail=(
                f"volume {volume_ratio:.2f}x son EWMA {EWMA_WINDOW_DAYS}j MAIS TVL "
                f"{tvl_trend_pct:+.1f}% sur {window}j -- pic non confirme, probablement toxique"
            ),
            volume_ratio_to_ewma=round(volume_ratio, 3),
            tvl_trend_pct=round(tvl_trend_pct, 1), history_days=history_days,
        )

    return ChainRegimeReading(
        chain=chain, regime=REGIME_INFLOW,
        detail=(
            f"volume {volume_ratio:.2f}x son EWMA {EWMA_WINDOW_DAYS}j, TVL "
            f"{tvl_trend_pct:+.1f}% sur {window}j -- afflux confirme"
            if tvl_trend_pct is not None else
            f"volume {volume_ratio:.2f}x son EWMA {EWMA_WINDOW_DAYS}j -- TVL non confirmable"
        ),
        volume_ratio_to_ewma=round(volume_ratio, 3),
        tvl_trend_pct=round(tvl_trend_pct, 1) if tvl_trend_pct is not None else None,
        history_days=history_days,
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chain_liquidity_regime (
                chain TEXT PRIMARY KEY,
                regime TEXT NOT NULL,
                detail TEXT NOT NULL,
                volume_ratio_to_ewma REAL,
                tvl_trend_pct REAL,
                history_days INTEGER NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def upsert_reading(reading: ChainRegimeReading) -> None:
    """ALWAYS overwrites this chain's previous reading -- same "no
    expiration" doctrine as market_sentiment.upsert_reading."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO chain_liquidity_regime
                (chain, regime, detail, volume_ratio_to_ewma, tvl_trend_pct, history_days, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain) DO UPDATE SET
                regime=excluded.regime, detail=excluded.detail,
                volume_ratio_to_ewma=excluded.volume_ratio_to_ewma,
                tvl_trend_pct=excluded.tvl_trend_pct,
                history_days=excluded.history_days, computed_at=excluded.computed_at
            """,
            (
                reading.chain, reading.regime, reading.detail,
                reading.volume_ratio_to_ewma, reading.tvl_trend_pct,
                reading.history_days, now,
            ),
        )
        await db.commit()


async def latest_regime(chain: str) -> dict | None:
    """Pure local DB read, zero network call -- the heartbeat refreshes
    separately, same property as market_sentiment.latest_readings()."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM chain_liquidity_regime WHERE chain = ?", (chain,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def run_chain_regime_cycle(chain: str) -> ChainRegimeReading:
    """Fetches both DefiLlama series for ``chain``, classifies, persists.

    Network failure on either series degrades to REGIME_INSUFFICIENT (never
    an invented regime) -- the previous reading in the table is simply not
    refreshed this cycle, same fail-safe as every other regime gate."""
    tvl_slug = _TVL_CHAIN_SLUG.get(chain.lower(), chain)
    tvl_series = await defillama.get_chain_tvl_history(tvl_slug)
    volume_series = await defillama.get_chain_dex_volume(chain)

    if not tvl_series.available or not volume_series.available:
        reading = ChainRegimeReading(
            chain=chain, regime=REGIME_INSUFFICIENT,
            detail=tvl_series.error or volume_series.error or defillama.UNAVAILABLE,
            volume_ratio_to_ewma=None, tvl_trend_pct=None, history_days=0,
        )
    else:
        reading = classify_chain_regime(chain, tvl_series.points, volume_series.points)

    await upsert_reading(reading)
    return reading
