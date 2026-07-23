"""VC prediction journal — measures ARIA's RELEVANCE over time.

Every `/vc` analysis is logged as a **dated prediction** (even without a real
trade: a "shadow" one). Later, the operator attributes a real outcome
(P&L %) via `/vcresult`. This lets us measure whether ARIA is genuinely relevant:

- **hit-rate**: share of BUY recommendations whose outcome is positive;
- **average P&L** per recommendation;
- **calibration**: does a "Potential 8/10" really outperform a
  "5/10"? (the real test of an analyst).

Logging *every* analysis (not just trades) speeds up accumulating a
statistically exploitable sample — at ~2 trades/month, limiting it to
real positions would be far too slow.

Local SQLite storage in `aria.db`, table `vc_prediction` (pure addition,
`CREATE TABLE IF NOT EXISTS`). No financial action: this is a journal.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "contract",
    "recommandation",
    "potentiel",
    "risque",
    "taille_pct",
    "security_score",
    "llm_used",
    "report_ref",
    "traded",
    "status",
    "outcome_pct",
    "outcome_note",
    "created_at",
    "closed_at",
    # "Tracked wallet" additions (vault 2) — additive, all nullable, hot-migrated
    # for existing DBs (no old row broken).
    "strategy",           # 'vc' (85% mid/long term) | 'spec' (15% small-cap speculation)
    "entry_price",        # USD price at verdict time (to value it live)
    "pool_address",       # DEX pool (to refresh the price via OHLCV)
    "network",            # GeckoTerminal network (Base at launch)
    "target_price",       # derived target (facts-only)
    "invalidation_price", # derived invalidation level
]

# Columns added afterward: (name, SQL definition) for the ALTER migration.
_ADDED_COLUMNS = [
    ("strategy", "TEXT DEFAULT 'vc'"),
    ("entry_price", "REAL"),
    ("pool_address", "TEXT"),
    ("network", "TEXT"),
    ("target_price", "REAL"),
    ("invalidation_price", "REAL"),
]

# Target allocation of ARIA's tracked portfolio (documented, never a real order).
STRATEGY_ALLOCATION = {"vc": 0.85, "spec": 0.15}

# Potential buckets for the calibration curve.
_CALIB_BUCKETS = [(0, 3, "0-3"), (4, 6, "4-6"), (7, 8, "7-8"), (9, 10, "9-10")]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vc_prediction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                recommandation TEXT NOT NULL,
                potentiel INTEGER,
                risque TEXT,
                taille_pct REAL DEFAULT 0,
                security_score INTEGER,
                llm_used INTEGER DEFAULT 0,
                report_ref TEXT,
                traded INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                outcome_pct REAL,
                outcome_note TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                strategy TEXT DEFAULT 'vc',
                entry_price REAL,
                pool_address TEXT,
                network TEXT,
                target_price REAL,
                invalidation_price REAL
            )
            """
        )
        # Hot migration: adds the "tracked wallet" columns to existing DBs
        # (SQLite doesn't create them if the table pre-exists). Idempotent, non-destructive.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(vc_prediction)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE vc_prediction ADD COLUMN {name} {ddl}")
        await db.commit()


async def record_prediction(
    *,
    contract: str,
    recommandation: str,
    potentiel: int | None,
    risque: str,
    taille_pct: float,
    security_score: int,
    llm_used: bool,
    report_ref: str = "",
    traded: bool = False,
    strategy: str = "vc",
    entry_price: float | None = None,
    pool_address: str = "",
    network: str = "",
    target_price: float | None = None,
    invalidation_price: float | None = None,
) -> int:
    """Records an ``open`` VC prediction and returns its id.

    The "tracked wallet" fields (strategy, entry_price, pool_address, network,
    target/invalidation) are optional: without them, behavior is identical
    to before (the verdict is logged but can't be valued live). With
    entry_price + pool_address, the position becomes valuable at the real OHLCV price.
    ``strategy`` in {'vc', 'spec'} — the 85/15 sleeve (small-cap speculation).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    strat = strategy if strategy in STRATEGY_ALLOCATION else "vc"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO vc_prediction
            (contract, recommandation, potentiel, risque, taille_pct, security_score,
             llm_used, report_ref, traded, status, created_at,
             strategy, entry_price, pool_address, network, target_price, invalidation_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract,
                recommandation,
                potentiel,
                risque,
                taille_pct,
                security_score,
                1 if llm_used else 0,
                report_ref,
                1 if traded else 0,
                now,
                strat,
                entry_price,
                pool_address or "",
                network or "",
                target_price,
                invalidation_price,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def close_prediction(prediction_id: int, *, outcome_pct: float, note: str = "") -> dict | None:
    """Attributes a real outcome (P&L %). Atomic ``open -> closed`` transition.

    Returns the closed row, or ``None`` if the id is unknown / already closed (an
    already-attributed outcome is never overwritten).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE vc_prediction
            SET status = 'closed', outcome_pct = ?, outcome_note = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (outcome_pct, note, now, prediction_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def get_prediction(prediction_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT * FROM vc_prediction WHERE id = ?", (prediction_id,))).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def count_predictions_for_contract(contract: str) -> int:
    """Number of analyses already recorded for this contract (before the current one).

    Used to number reports ("Report #2 on this token") so a
    subscriber receiving several tracked analyses of the same token can find
    their way. Case-insensitive comparison (EVM address).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM vc_prediction WHERE LOWER(contract) = LOWER(?)",
                (contract,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def list_predictions_for_contract(contract: str, limit: int = 50) -> list[dict]:
    """Full history of VC analyses for a contract, most recent first.

    Feeds the "per-token file" (analysis timeline). Case-insensitive
    comparison (EVM address stored as-is by the writer).
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE LOWER(contract) = LOWER(?) "
                "ORDER BY id DESC LIMIT ?",
                (contract, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def total_predictions_count() -> int:
    """Total number of ARIA analyses ever recorded (across all tokens).

    Serves as a global serial number ("Series 00.047") — gives the report a
    numbered edition identity, independent of per-token tracking.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM vc_prediction")).fetchone()
    return int(row[0]) if row else 0


async def list_open_predictions(limit: int = 20) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE status = 'open' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def list_recently_closed(since_iso: str, limit: int = 20) -> list[dict]:
    """Predictions closed since ``since_iso`` (ISO 8601), most recent first.

    Serves as the source for post-closure analysis engines (e.g. pump/dump autopsy) —
    read-only, no writes, reuses the existing table."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM vc_prediction WHERE status = 'closed' AND closed_at >= ? "
                "ORDER BY closed_at DESC LIMIT ?",
                (since_iso, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def compute_metrics(predictions: list[dict]) -> dict:
    """Relevance metrics from a list of predictions (pure function, testable).

    Only considers closed predictions (real outcome attributed) for the
    rates; counts open ones separately.
    """
    closed = [p for p in predictions if p.get("status") == "closed" and p.get("outcome_pct") is not None]
    open_count = sum(1 for p in predictions if p.get("status") == "open")

    buys = [p for p in closed if p.get("recommandation") == "BUY"]
    wins = [p for p in buys if (p.get("outcome_pct") or 0) > 0]
    losses = [p for p in buys if (p.get("outcome_pct") or 0) <= 0]
    hit_rate = (len(wins) / len(buys)) if buys else None
    avg_pnl_buy = (sum(p["outcome_pct"] for p in buys) / len(buys)) if buys else None
    # Separate magnitudes (winners vs losers) — needed for Kelly sizing
    # (a blended avg_pnl_buy isn't enough: Kelly needs the win/loss ratio, not the net).
    avg_win_pct = (sum(p["outcome_pct"] for p in wins) / len(wins)) if wins else None
    avg_loss_pct = (sum(p["outcome_pct"] for p in losses) / len(losses)) if losses else None

    # Calibration: average P&L per potential bucket (LLM-scored analyses only).
    calibration = []
    scored = [p for p in closed if p.get("potentiel") is not None]
    for low, high, label in _CALIB_BUCKETS:
        bucket = [p for p in scored if low <= p["potentiel"] <= high]
        if bucket:
            avg = sum(p["outcome_pct"] for p in bucket) / len(bucket)
            calibration.append({"bucket": label, "count": len(bucket), "avg_pnl": avg})

    # "Wall of NO": AVOID verdicts (all statuses) — the strongest public proof.
    avoid_count = sum(1 for p in predictions if p.get("recommandation") == "AVOID")

    # Breakdown by 85/15 sleeve (BUY hit-rate by strategy).
    by_strategy = {}
    for sleeve in ("vc", "spec"):
        s_buys = [p for p in buys if (p.get("strategy") or "vc") == sleeve]
        s_wins = [p for p in s_buys if (p.get("outcome_pct") or 0) > 0]
        by_strategy[sleeve] = {
            "buy_count": len(s_buys),
            "hit_rate": (len(s_wins) / len(s_buys)) if s_buys else None,
            "avg_pnl_buy": (sum(p["outcome_pct"] for p in s_buys) / len(s_buys)) if s_buys else None,
        }

    return {
        "total": len(predictions),
        "closed": len(closed),
        "open": open_count,
        "buy_count": len(buys),
        "hit_rate": hit_rate,
        "avg_pnl_buy": avg_pnl_buy,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "calibration": calibration,
        "avoid_count": avoid_count,
        "by_strategy": by_strategy,
    }


async def list_all_predictions() -> list[dict]:
    """All predictions (open and closed), no filter. Used by
    cross-cutting analyses (e.g. the "ready for real money" scorecard) that need
    the entire journal, not just an already-aggregated subset."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("SELECT * FROM vc_prediction")).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def metrics() -> dict:
    """Loads all predictions and computes the relevance metrics."""
    return compute_metrics(await list_all_predictions())


async def format_track_report() -> str:
    """VC relevance text report -- extracted from _handle_track (telegram_bot.py,
    07/18, #213) to be reusable by the natural-language router, a single
    source of truth for "track record" regardless of the trigger."""
    m = await metrics()
    if m["total"] == 0:
        return "Aucune prédiction enregistrée. Lance des analyses avec /vc."

    lines = [
        "📈 Pertinence ARIA — track record VC",
        f"Prédictions : {m['total']} ({m['closed']} clôturées, {m['open']} ouvertes)",
    ]
    if m["hit_rate"] is not None:
        lines.append(f"Hit-rate BUY : {m['hit_rate']:.0%} sur {m['buy_count']} BUY clôturés")
        lines.append(f"P&L moyen BUY : {m['avg_pnl_buy']:+.1f}%")
    else:
        lines.append("Pas encore de BUY clôturé — clôture avec /vcresult pour mesurer.")

    if m["calibration"]:
        lines.append("")
        lines.append("Calibration (Potentiel → P&L moyen réel) :")
        for b in m["calibration"]:
            lines.append(f"  {b['bucket']}/10 : {b['avg_pnl']:+.1f}% (n={b['count']})")
        lines.append("Idéal : le P&L croît avec le potentiel.")

    return "\n".join(lines)


def _sleeve_return(holdings: list[dict], current_prices: dict[int, float]) -> tuple[float, int]:
    """Average (equal-weight) return of a sleeve + number of valued positions."""
    rets = []
    for p in holdings:
        entry = p.get("entry_price")
        cur = current_prices.get(p["id"])
        if entry and cur and entry > 0:
            rets.append((cur - entry) / entry)
    if not rets:
        return 0.0, 0
    return sum(rets) / len(rets), len(rets)


def portfolio_value(
    predictions: list[dict], current_prices: dict[int, float], *, base_index: float = 100.0
) -> dict:
    """Value of ARIA's TRACKED portfolio (paper, never a real fund).

    Honest, documented model: only **BUY** verdicts are held (what ARIA
    "would hold"), equal-weight within each sleeve, split **85% VC / 15%
    speculation**. A sleeve with no position is worth 0 (idle cash, flat return).
    The index starts at ``base_index`` (100) -> the value reflects the unrealized
    return, valued at the **real OHLCV prices** supplied in ``current_prices``.
    Pure function: no network here (prices are injected), hence testable.
    """
    buys = [p for p in predictions if p.get("recommandation") == "BUY"]
    vc = [p for p in buys if (p.get("strategy") or "vc") == "vc"]
    spec = [p for p in buys if (p.get("strategy") or "vc") == "spec"]

    vc_ret, n_vc = _sleeve_return(vc, current_prices)
    spec_ret, n_spec = _sleeve_return(spec, current_prices)

    total_ret = (
        STRATEGY_ALLOCATION["vc"] * vc_ret + STRATEGY_ALLOCATION["spec"] * spec_ret
    )
    return {
        "index": round(base_index * (1 + total_ret), 2),
        "total_return_pct": round(total_ret * 100, 2),
        "vc_return_pct": round(vc_ret * 100, 2),
        "spec_return_pct": round(spec_ret * 100, 2),
        "positions_valued": n_vc + n_spec,
        "vc_positions": n_vc,
        "spec_positions": n_spec,
        "allocation": dict(STRATEGY_ALLOCATION),
    }


async def _current_prices_for(predictions: list[dict]) -> dict[int, float]:
    """Fetches the last real OHLCV price of valuable positions (BUY + pool).

    Graceful degradation: a position whose pool is absent or whose OHLCV
    isn't available is simply omitted (never an invented price).
    """
    from aria_core.services.ohlcv import ohlcv_client

    prices: dict[int, float] = {}
    for p in predictions:
        if p.get("recommandation") != "BUY":
            continue
        pool = (p.get("pool_address") or "").strip()
        if not pool:
            continue
        res = await ohlcv_client.get_ohlcv(pool, network=(p.get("network") or "base"))
        if res.available and res.candles:
            prices[p["id"]] = res.candles[-1].close
    return prices


async def live_wallet() -> dict:
    """Live value of ARIA's tracked portfolio (open BUY positions, real prices).

    This is the "ARIA wallet" figure meant for the homepage (FOMO teaser)
    and the subscriber page. Facts-only: if there's no valuable position, a
    neutral index (100, +0%) is returned — never an inflated figure.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute("SELECT * FROM vc_prediction WHERE status = 'open'")
        ).fetchall()
    open_preds = [dict(zip(_COLUMNS, row)) for row in rows]
    prices = await _current_prices_for(open_preds)
    return portfolio_value(open_preds, prices)
