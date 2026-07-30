"""Item #247 (30/07), operator request ("créer un log qui référence
l'inclinaison de la divergence en degré pour savoir lesquelles sont acheter,
lequel fonctionne le mieux, lesquels sont souvent refuser") -- a dedicated,
append-only log of every REAL bullish RSI divergence decision point (never
a plain HOLD where no golden-pocket/RSI setup was ever evaluated), so a
later analysis can correlate divergence "steepness" against outcome.

``gap``/``span`` (RSI points / candles) already exist on
``entry_signals.RsiDivergenceDetail``/``EntrySignal`` (Item #183, 28/07) --
the operator's own framing there was "netteté/brièveté" (sharpness/
briefness) as a proxy for reversal strength, never previously reduced to a
single number nor persisted anywhere. This module adds exactly that:

``angle_deg`` -- the incline of the (span, gap) segment, in degrees,
via ``atan2(gap, span)``. Treats candles (x-axis) and RSI points (y-axis)
as plain Cartesian coordinates: a divergence recovering MANY RSI points
over FEW candles (a sharp, fast reversal) reads as a steep angle (close to
90 deg); one recovering few points over many candles (a slow, shallow
recovery) reads as a shallow angle (close to 0 deg). This is a DEFINITION
specific to this project (there is no standard "RSI divergence angle" in
technical analysis) -- documented here so it's never re-derived
differently elsewhere. ``None`` whenever ``gap``/``span`` aren't both
known (never a fabricated angle).

Four outcomes (``OUTCOMES``) are logged, covering the operator's three
questions (bought / performs best / often refused):
- ``bought_direct`` -- the divergence was ALREADY confirmed at the same
  scan that decided BUY (``momentum_entry.evaluate_momentum_entry``,
  no limit order ever involved). Angle always present (a confirmed
  divergence always carries gap/span, see ``entry_signals.detect_entry``).
- ``bought_via_limit_order`` -- price reached the golden pocket first
  (``_rsi_divergence_watch_candidate``), a limit order watched for the
  divergence to form, and it eventually confirmed and triggered a real buy
  (``limit_orders.check_rsi_divergence_watching_order``). Angle present
  (the CONFIRMED divergence at trigger time, re-checked on fresh candles
  -- may differ from any earlier partial read).
- ``expired_unconfirmed`` / ``cancelled_unconfirmed`` -- a watch was placed
  but the divergence never confirmed in time (candle-count horizon
  elapsed) or the setup died first (invalidation crossed -- the only
  cancellation reason left on this path since ``is_market_dead`` was
  removed, Item #251, 30/07). No angle -- there is nothing to measure when
  no divergence ever completed; these rows answer "how often is a
  divergence attempt refused" by their sheer COUNT, not by an angle value.

Append-only (same doctrine as ``momentum_scan_log.py``/``momentum_
blacklist.py``) -- one row per decision point, never updated/deleted.
Best-effort: a write failure here must never break a real trading cycle."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

OUTCOMES = (
    "bought_direct",
    "bought_via_limit_order",
    "expired_unconfirmed",
    "cancelled_unconfirmed",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_angle_deg(gap: float | None, span: int | None) -> float | None:
    """The (span, gap) incline in degrees -- see module docstring for the
    exact definition and rationale. ``None`` if either input is missing or
    ``span`` is non-positive (no real candle distance to measure an incline
    over -- never a fabricated angle)."""
    if gap is None or span is None or span <= 0:
        return None
    return math.degrees(math.atan2(gap, span))


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS rsi_divergence_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                wallet TEXT,
                mode TEXT,
                gap REAL,
                span INTEGER,
                angle_deg REAL,
                outcome TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rsi_divergence_log_recorded_at "
            "ON rsi_divergence_log (recorded_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rsi_divergence_log_outcome "
            "ON rsi_divergence_log (outcome)"
        )
        await db.commit()


async def record_divergence(
    contract: str, chain: str, *, outcome: str, symbol: str | None = None,
    wallet: str | None = None, mode: str | None = None,
    gap: float | None = None, span: int | None = None,
) -> None:
    """Records one divergence decision point. ``outcome`` must be one of
    ``OUTCOMES`` (defensive assert -- a typo here would silently create an
    unanalyzable bucket). Best-effort: never raises into the caller's real
    trading cycle."""
    if not contract or outcome not in OUTCOMES:
        return
    angle_deg = compute_angle_deg(gap, span)
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO rsi_divergence_log "
                "(contract, chain, symbol, wallet, mode, gap, span, angle_deg, outcome, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    contract.lower(), chain or "base", symbol, wallet, mode,
                    gap, span, angle_deg, outcome, _now(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort telemetry, never blocking
        logger.info("rsi_divergence_log: write failed for %s (%s)", contract, exc)


async def recent_entries(limit: int = 20) -> list[dict]:
    """Most recent rows, newest first -- capped, never an unbounded dump."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT contract, chain, symbol, wallet, mode, gap, span, angle_deg, outcome, recorded_at "
            "FROM rsi_divergence_log ORDER BY recorded_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def summarize_by_outcome() -> dict[str, dict]:
    """One bucket per outcome: count + average angle + average span
    (``None`` if no row in that bucket ever carried one, e.g. every
    expired/cancelled row). Span (Item #250, 30/07, operator request "aussi
    la longueur de la divergence") is the raw candle-count length the angle
    is derived from -- shown alongside it since two divergences can share
    the same angle (same gap/span RATIO) while spanning very different
    real durations. The whole point of this summary -- "which ones get
    bought, which one performs/converges best, which ones are often
    refused" -- is a direct read of these counts/averages, no separate
    report needed."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT outcome, COUNT(*), AVG(angle_deg), MIN(angle_deg), MAX(angle_deg), "
            "AVG(span), MIN(span), MAX(span) "
            "FROM rsi_divergence_log GROUP BY outcome"
        )
        rows = await cursor.fetchall()
    out: dict[str, dict] = {
        o: {
            "count": 0, "avg_angle_deg": None, "min_angle_deg": None, "max_angle_deg": None,
            "avg_span": None, "min_span": None, "max_span": None,
        }
        for o in OUTCOMES
    }
    for outcome, count, avg_angle, min_angle, max_angle, avg_span, min_span, max_span in rows:
        out[outcome] = {
            "count": count,
            "avg_angle_deg": avg_angle,
            "min_angle_deg": min_angle,
            "max_angle_deg": max_angle,
            "avg_span": avg_span,
            "min_span": min_span,
            "max_span": max_span,
        }
    return out


def format_summary_report(summary: dict[str, dict]) -> str:
    """Operator-facing text, same doctrine as every other diagnostic report
    in this codebase -- degrades honestly (no fabricated average) when a
    bucket has zero rows or no angle ever recorded in it."""
    labels = {
        "bought_direct": "Achetées directement",
        "bought_via_limit_order": "Achetées via ordre limite",
        "expired_unconfirmed": "Expirées (jamais confirmées)",
        "cancelled_unconfirmed": "Annulées (jamais confirmées)",
    }
    lines = ["📐 Log des divergences RSI (inclinaison en degré)"]
    for outcome in OUTCOMES:
        bucket = summary.get(outcome, {})
        count = bucket.get("count", 0)
        avg = bucket.get("avg_angle_deg")
        if count == 0:
            lines.append(f"- {labels[outcome]} : aucune entrée")
        elif avg is None:
            lines.append(f"- {labels[outcome]} : {count} (angle non mesurable)")
        else:
            lo_angle = bucket.get("min_angle_deg")
            hi_angle = bucket.get("max_angle_deg")
            line = f"- {labels[outcome]} : {count} | angle moyen {avg:.1f}° (min {lo_angle:.1f}°, max {hi_angle:.1f}°)"
            avg_span = bucket.get("avg_span")
            if avg_span is not None:
                lo_span = bucket.get("min_span")
                hi_span = bucket.get("max_span")
                line += f" | longueur moyenne {avg_span:.1f} bougies (min {lo_span:.0f}, max {hi_span:.0f})"
            lines.append(line)
    return "\n".join(lines)
