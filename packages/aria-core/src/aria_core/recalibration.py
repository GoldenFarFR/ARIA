"""Recalibration requests — ARIA escalates when it CANNOT judge with confidence.

Operator principle (dome extension): **total transparency required**. No
transparency, no trust. BUT rather than rejecting a promising token in the
dark (a definitive false negative), ARIA **raises a request** to the
operator to recalibrate the analysis: "this token interests me but I'm
missing X to decide — how do you want me to judge it?"

Trigger: a **promising** token (real liquidity/activity) but **opaque**
(unverified contract, mint authority undeterminable, unknown distribution,
LP-lock unconfirmable...). Obvious scams do NOT get escalated (noise) —
only cases where the opacity PREVENTS a good judgment.

Local SQLite storage in `aria.db`, table `recalibration_request` (key =
contract). No financial action: this is a queue of questions for the human.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


# Expected transparency dimensions. An "unknown" (unavailable) data point, not
# a "bad" one, makes the token UNJUDGEABLE with confidence -> a recalibration candidate.
@dataclass(frozen=True)
class TransparencyVerdict:
    """Is the token transparent enough to be judged, and if not, what's missing?"""

    transparent: bool
    missing: list[str] = field(default_factory=list)


def assess_transparency(ctx) -> TransparencyVerdict:
    """Assesses whether the facts needed for a reliable judgment are ALL accessible.

    Pure, deterministic. Does NOT judge quality (good/bad) — only whether the
    info EXISTS. One missing point = opacity = unjudgeable with confidence.
    """
    missing: list[str] = []
    if ctx.contract_verified is not True:
        missing.append("contrat non vérifié (code source inaccessible)")
    # If an external mint exists but its authority couldn't be resolved.
    if ctx.has_mint is True and (ctx.mint_authority in (None, "unknown")):
        missing.append("autorité du mint indéterminable (renoncé/launchpad/dev ?)")
    if ctx.top_holder_pct is None:
        missing.append("distribution des holders inconnue")
    return TransparencyVerdict(transparent=not missing, missing=missing)


def is_promising(ctx, *, min_liquidity_usd: float = 10_000.0) -> bool:
    """Is the token worth a human look? (real activity, not dust).

    Only tokens with a real pair and non-trivial liquidity get escalated: no
    point bothering the operator for a dead pool or an obvious scam.
    """
    if ctx.best_pair is None:
        return False
    return (ctx.best_pair.liquidity_usd or 0.0) >= min_liquidity_usd


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS recalibration_request (
                contract TEXT PRIMARY KEY,
                symbol TEXT,
                reason TEXT,
                missing TEXT,
                promising_signals TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution TEXT
            )
            """
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def request_recalibration(
    contract: str,
    *,
    symbol: str = "",
    reason: str = "",
    missing: list[str] | None = None,
    promising_signals: list[str] | None = None,
) -> bool:
    """Records (or refreshes) a 'pending' recalibration request. Idempotent.

    Returns True if this is a NEW request (useful to notify the operator only
    once), False if it was already pending.
    """
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT status FROM recalibration_request WHERE contract = ?", (contract,)
        )
        row = await cur.fetchone()
        is_new = row is None or row[0] != "pending"
        await db.execute(
            """
            INSERT INTO recalibration_request
                (contract, symbol, reason, missing, promising_signals, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(contract) DO UPDATE SET
                symbol=excluded.symbol, reason=excluded.reason, missing=excluded.missing,
                promising_signals=excluded.promising_signals, status='pending',
                created_at=excluded.created_at, resolved_at=NULL, resolution=NULL
            """,
            (
                contract,
                symbol,
                reason,
                json.dumps(missing or [], ensure_ascii=False),
                json.dumps(promising_signals or [], ensure_ascii=False),
                _now(),
            ),
        )
        await db.commit()
    return is_new


async def maybe_escalate(ctx, *, symbol: str = "") -> bool:
    """Decides and records: escalates if the token is PROMISING but OPAQUE.

    Returns True if a new recalibration request was created. Does nothing
    (False) if the token is transparent, or not promising enough to bother.
    """
    if not is_promising(ctx):
        return False
    verdict = assess_transparency(ctx)
    if verdict.transparent:
        return False
    liq = ctx.best_pair.liquidity_usd if ctx.best_pair else 0.0
    signals = [f"liquidité ${liq:,.0f}", f"score {ctx.security_score}"]
    return await request_recalibration(
        ctx.contract,
        symbol=symbol,
        reason="prometteur mais opaque : transparence insuffisante pour juger",
        missing=verdict.missing,
        promising_signals=signals,
    )


async def list_pending(limit: int = 20) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM recalibration_request WHERE status = 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["missing"] = json.loads(d.get("missing") or "[]")
        d["promising_signals"] = json.loads(d.get("promising_signals") or "[]")
        out.append(d)
    return out


async def count_pending() -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM recalibration_request WHERE status = 'pending'"
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def resolve_request(contract: str, resolution: str = "") -> None:
    """Marks a request as processed (the operator has recalibrated / decided)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE recalibration_request SET status='resolved', resolved_at=?, "
            "resolution=? WHERE contract=?",
            (_now(), resolution, contract),
        )
        await db.commit()
