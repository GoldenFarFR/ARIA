"""Investment memory loop — thesis -> decision -> outcome/P&L -> lesson.

ARIA's reasoning journal on its bets, entirely local (SQLite ``aria.db``,
``investment_thesis`` table). No financial action, no signing, no network
call: this is a trace to attribute an outcome to each decision and draw a
lesson from it — the prerequisite for a VC scoring engine that learns from
its mistakes (cf. AGENTS.md, "honest self-critique" rule).

Lifecycle of a row:
- ``open`` when the thesis is recorded (``record_thesis``);
- single ``open -> closed`` transition via ``close_thesis`` (atomic — an
  outcome is attributed only once, history is never rewritten).

The table is created lazily via ``CREATE TABLE IF NOT EXISTS`` — a pure
addition, no alteration of the existing schema, so no Alembic migration is
needed; the ``/opt/aria-data`` backup remains covered by the deployment
procedure (``docs/deploy-ionos.md``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Allowed investment decisions (pure journal — never an execution order).
VALID_DECISIONS = ("BUY", "WATCH", "SELL", "AVOID")

_COLUMNS = [
    "id",
    "token_address",
    "token_symbol",
    "thesis",
    "decision",
    "score_snapshot",
    "created_at",
    "status",
    "outcome",
    "lesson",
    "closed_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_thesis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                thesis TEXT NOT NULL,
                decision TEXT NOT NULL,
                score_snapshot TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                outcome TEXT,
                lesson TEXT,
                closed_at TEXT
            )
            """
        )
        await db.commit()


async def record_thesis(
    *,
    token_address: str,
    thesis: str,
    decision: str,
    token_symbol: str | None = None,
    score_snapshot: str = "{}",
) -> int:
    """Records an ``open`` thesis and returns its id.

    ``decision`` must be one of ``VALID_DECISIONS`` (upstream validation on the
    caller side is recommended — ``ValueError`` otherwise).
    """
    decision = decision.upper()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"décision invalide : {decision!r} (attendu : {', '.join(VALID_DECISIONS)})")

    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO investment_thesis
            (token_address, token_symbol, thesis, decision, score_snapshot, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'open')
            """,
            (token_address, token_symbol, thesis, decision, score_snapshot, now),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def close_thesis(thesis_id: int, *, outcome: str, lesson: str) -> dict | None:
    """Atomic ``open -> closed`` transition (attributes outcome + lesson).

    Returns the closed row if the transition took place, otherwise ``None``
    (unknown id or already closed — an already-attributed outcome is never
    rewritten).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE investment_thesis
            SET status = 'closed', outcome = ?, lesson = ?, closed_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (outcome, lesson, now, thesis_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row_cursor = await db.execute("SELECT * FROM investment_thesis WHERE id = ?", (thesis_id,))
        row = await row_cursor.fetchone()
    if not row:
        return None
    return dict(zip(_COLUMNS, row))


async def get_thesis(thesis_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row_cursor = await db.execute("SELECT * FROM investment_thesis WHERE id = ?", (thesis_id,))
        row = await row_cursor.fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def list_open_theses(limit: int = 20) -> list[dict]:
    """Theses still open (outcome not yet attributed), most recent first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row_cursor = await db.execute(
            "SELECT * FROM investment_thesis WHERE status = 'open' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await row_cursor.fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def list_theses_for_token(token_address: str, limit: int = 10) -> list[dict]:
    """History of theses (open + closed) for a token, most recent first.

    Serves as factual context for the VC engine: "what has already been bet
    on this token, and what has been learned from it?".
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row_cursor = await db.execute(
            "SELECT * FROM investment_thesis WHERE LOWER(token_address) = LOWER(?) "
            "ORDER BY id DESC LIMIT ?",
            (token_address, limit),
        )
        rows = await row_cursor.fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
