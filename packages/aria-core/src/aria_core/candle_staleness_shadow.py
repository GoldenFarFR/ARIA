"""Candle-freshness shadow observer (backlog #261, 10/08) -- logs, NEVER blocks.

Real gap this closes: `momentum_entry.py`'s only two existing candle-quality
checks are `_candles_price_consistent` (a scale sanity check against
DexScreener's spot price) and the scalping-only "continuity gate" comparing
`candles[-1].ts - candles[-2].ts` against the series' own median cadence --
that gate detects TRADING SILENCE (no trade happened), never PROVIDER
STALENESS (the API keeps returning HTTP 200 with a candle series that simply
stopped advancing, e.g. during a Base propagation-congestion incident like
the real one confirmed 31/01/2026). A provider stuck re-serving the same
stale tail would sail through both existing checks -- the internal gaps
between old candles stay perfectly regular, only the gap between the LAST
candle and WALL-CLOCK NOW grows unboundedly, which neither check measures.

Same anti-overfitting doctrine locked this session (v8 wick-gate
methodology, `docs/HANDOFF_PIPELINE_MOMENTUM.md` 2026.08.10): no real
calibration data exists yet on how often genuine staleness occurs or what
multiplier cleanly separates it from normal fetch/processing jitter --
promoting a guessed threshold straight to a hard gate risks silently
rejecting good candidates with zero empirical backing. This module only
LOGS a candidate verdict (shadow mode) so the threshold can be validated
against real observations before ever gating a real decision, exactly the
pattern `wick_filter_shadow.py`/`chasing_filter_shadow.py` already
established.

Same design as `wick_filter_shadow.py` (deliberately mirrored): dedicated
append-only table, best-effort writes that NEVER raise into a real fetch
path, per-DB-path ensure cache.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Candidate threshold under shadow evaluation -- NOT yet validated against
# real observations. Reasoned starting point, not a guess pulled from
# nowhere: the existing scalping continuity gate already uses 4.0x the
# median candle interval to flag "too old" for TRADING SILENCE
# (`_SCALPING_MAX_CANDLE_GAP_MULTIPLIER`, momentum_entry.py); staleness-to-
# NOW deserves a bit more slack than that (normal fetch/processing latency
# alone eats into it, and this applies to every mode, not just scalping's
# tight cadence) -- 5.0x chosen as a deliberately slightly wider starting
# candidate, to be tightened or loosened once shadow data accumulates.
STALENESS_SHADOW_MULTIPLIER = 5.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # wick_filter_shadow.py/chasing_filter_shadow.py.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS candle_staleness_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                mode TEXT NOT NULL,
                source TEXT NOT NULL,
                age_seconds REAL,
                median_interval_seconds REAL,
                would_flag INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_candle_staleness_shadow_recorded_at "
            "ON candle_staleness_shadow_log (recorded_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_observation(
    contract: str,
    chain: str,
    *,
    mode: str,
    source: str,
    age_seconds: float | None,
    median_interval_seconds: float | None,
    symbol: str | None = None,
) -> None:
    """Logs one shadow observation on a real (non-cached) candle fetch.
    Best-effort: NEVER raises into the caller's fetch path (same contract as
    ``wick_filter_shadow.record_trigger``). ``would_flag`` stays ``None``
    when either input is unknown (fewer than 2 candles, or a degenerate
    interval) -- never a fabricated verdict on data too thin to judge."""
    if not contract:
        return
    would_flag = None
    if age_seconds is not None and median_interval_seconds and median_interval_seconds > 0:
        would_flag = 1 if age_seconds > STALENESS_SHADOW_MULTIPLIER * median_interval_seconds else 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO candle_staleness_shadow_log (
                    contract, chain, symbol, mode, source,
                    age_seconds, median_interval_seconds, would_flag, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract, chain or "base", symbol, mode, source,
                    age_seconds, median_interval_seconds, would_flag,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real fetch
        logger.info("candle_staleness_shadow: record failed (%s)", exc)


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first -- for the future forward-
    validation pass (how often would_flag=1 fires, and whether it correlates
    with anything worth gating on)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM candle_staleness_shadow_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def flagged_rate(limit: int = 500) -> float | None:
    """Fraction of recent observations where ``would_flag == 1`` -- ``None``
    if there's nothing judgeable yet (no rows, or every row's would_flag is
    ``None``). A cheap, honest summary for a session picking this thread back
    up later, without needing to hand-read raw rows first."""
    rows = await list_recent(limit=limit)
    judged = [r for r in rows if r.get("would_flag") is not None]
    if not judged:
        return None
    flagged = sum(1 for r in judged if r["would_flag"] == 1)
    return flagged / len(judged)


# 25/08, audit 001-audit-code-sans (T007): 37,717 observations had
# accumulated with a 10.1% flag rate, but the forward-validation pass this
# module's own docstring/list_recent() promised ("for the future forward-
# validation pass") had never been written -- enough volume to calibrate,
# zero analysis done. This closes that gap: does a flagged observation
# (would_flag=1, a candle series suspected stale at the moment a real
# momentum decision was being evaluated) actually precede a WORSE real
# outcome than a clean one, or is it just noise?
#
# Link method: record_observation() fires from momentum_entry._fetch_candles
# on every real candle fetch feeding a momentum decision -- the same
# (contract, chain) a paper_position opens on shortly after, if the
# candidate clears every other gate. FORWARD_LINK_WINDOW_MINUTES bounds how
# soon after the observation a position must have opened to count as the
# SAME decision (too wide a window would link an observation to an
# unrelated later re-entry on the same token).
FORWARD_LINK_WINDOW_MINUTES = 30.0

# Same statistical guardrail as every other bucket-comparison in this
# project (never conclude on a raw average -- retest minus the top 1-2).
_MIN_SAMPLES_PER_BUCKET = 5


async def forward_validation_report() -> dict:
    """Does a flagged staleness observation precede a real, worse trading
    outcome? Links each judgeable observation to the paper_position (if any)
    opened on the SAME (contract, chain) within FORWARD_LINK_WINDOW_MINUTES
    afterwards, buckets by would_flag, and compares realized pnl_pct --
    never a verdict below _MIN_SAMPLES_PER_BUCKET closed, linked positions on
    BOTH sides (returns an honest 'not enough data yet' instead), and never
    on the raw average alone (a top-1/2-trimmed figure decides the verdict,
    same guardrail as signal_cascade_convergence.falsifiability_report)."""
    _empty = {
        "n_flagged_linked": 0, "n_clean_linked": 0,
        "avg_pnl_pct_flagged": None, "avg_pnl_pct_clean": None,
        "avg_pnl_pct_flagged_no_top2": None, "avg_pnl_pct_clean_no_top2": None,
        "enough_data": False,
        "verdict": f"not enough linked samples yet (min {_MIN_SAMPLES_PER_BUCKET}/bucket required)",
    }
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute(
            "SELECT contract, chain, would_flag, recorded_at FROM candle_staleness_shadow_log "
            "WHERE would_flag IS NOT NULL"
        )
        observations = await cursor.fetchall()
        has_paper_position = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_position'"
        )
        if not await has_paper_position.fetchone():
            return dict(_empty)  # nothing to link against yet -- honest, not a crash
        cursor = await db.execute(
            "SELECT contract, chain, opened_at, pnl_pct FROM paper_position "
            "WHERE status = 'closed' AND pnl_pct IS NOT NULL"
        )
        positions = await cursor.fetchall()

    # Index positions by (contract, chain) once -- avoids an O(observations x
    # positions) table scan repeated per observation.
    by_key: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for contract, chain, opened_at, pnl_pct in positions:
        try:
            opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        by_key.setdefault((contract, chain), []).append((opened_dt, pnl_pct))

    buckets: dict[int, list[float]] = {0: [], 1: []}
    window = timedelta(minutes=FORWARD_LINK_WINDOW_MINUTES)
    for contract, chain, would_flag, recorded_at in observations:
        candidates = by_key.get((contract, chain))
        if not candidates:
            continue
        try:
            recorded_dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        # Earliest position opened within the forward window -- the decision
        # this observation actually fed, never a later unrelated re-entry.
        linked = min(
            (opened_dt for opened_dt, _ in candidates if recorded_dt <= opened_dt <= recorded_dt + window),
            default=None,
        )
        if linked is None:
            continue
        pnl_pct = next(p for opened_dt, p in candidates if opened_dt == linked)
        buckets[int(would_flag)].append(pnl_pct)

    def _avg(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    def _avg_no_top2(vals: list[float]) -> float | None:
        if len(vals) <= 2:
            return None
        trimmed = sorted(vals, reverse=True)[2:]
        return sum(trimmed) / len(trimmed)

    n_flagged, n_clean = len(buckets[1]), len(buckets[0])
    enough = n_flagged >= _MIN_SAMPLES_PER_BUCKET and n_clean >= _MIN_SAMPLES_PER_BUCKET
    avg_flagged, avg_clean = _avg(buckets[1]), _avg(buckets[0])
    avg_flagged_no_top2, avg_clean_no_top2 = _avg_no_top2(buckets[1]), _avg_no_top2(buckets[0])
    verdict_flagged = avg_flagged_no_top2 if avg_flagged_no_top2 is not None else avg_flagged
    verdict_clean = avg_clean_no_top2 if avg_clean_no_top2 is not None else avg_clean

    return {
        "n_flagged_linked": n_flagged,
        "n_clean_linked": n_clean,
        "avg_pnl_pct_flagged": round(avg_flagged, 2) if avg_flagged is not None else None,
        "avg_pnl_pct_clean": round(avg_clean, 2) if avg_clean is not None else None,
        "avg_pnl_pct_flagged_no_top2": round(avg_flagged_no_top2, 2) if avg_flagged_no_top2 is not None else None,
        "avg_pnl_pct_clean_no_top2": round(avg_clean_no_top2, 2) if avg_clean_no_top2 is not None else None,
        "enough_data": enough,
        "verdict": (
            (
                "flagged observations precede a real, worse outcome -- worth graduating past shadow"
                if verdict_flagged < verdict_clean else
                "no real correlation found yet -- stay in shadow mode"
            )
            if enough and verdict_flagged is not None and verdict_clean is not None
            else f"not enough linked samples yet (min {_MIN_SAMPLES_PER_BUCKET}/bucket required)"
        ),
    }
