"""Batch review of ARIA's losing trades, 10 at a time (07/24, direct operator
request after the real-capital risk design for the future ``aria-smart-vc``/
``aria-smart-st`` Smart Accounts (#41): "un suivi de tout les trades perdant,
traité par lot de 10 pour eviter l'isolation d'une malchance et comprendre et
réajuster la trajectoire" -- a per-trade alert was explicitly rejected first
(statistical noise, reacting to a single anecdote) in favor of this batched
mechanism.

Distinct from ``trade_devils_advocate.py`` (reviews ONE closed position,
"sound"/"flawed", a reasoning-flaw lens) -- this module never re-litigates a
single trade. It only fires once 10 NEW losing trades have accumulated since
the last batch, and asks a single question across all 10 at once: is there a
recurring PATTERN (same discovery channel, same conviction tier, same chain,
same regime, same close reason...) that a single trade could never reveal?
Deliberately conservative: a batch of 10 heterogeneous losses with no common
thread produces no pattern and no adjustment -- never a fabricated insight
forced out of noise (same "process over outcome" doctrine as the Devil's
Advocate).

One-way, same family as the Devil's Advocate/trailing-stop/breakeven-floor/
macro-regime doctrine: a confirmed trajectory adjustment can only tighten
future caution, never relax it -- nothing here removes/softens an
already-written adjustment.

Generic over ``positions_fetch`` (same seam as ``trade_devils_advocate.py``):
runs against the $1M paper portfolio today (there's already a rich, ready-to-
test history of closed trades there), and will run unchanged against a real
wallet's closed-position ledger once #41 (Smart Account migration) gives one
-- no rewrite needed, just a different fetch function passed in.

Persisted in SQLite (never a file committed to the ARIA repo -- same doctrine
as ``trade_devils_advocate.py``/``momentum_blacklist.py``).

Gated OFF by default (``ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED``), respects
``/stop``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

BATCH_SIZE = 10
_MAX_BATCHES_PER_CYCLE = 1  # LLM-cost sanity cap, same discipline as trade_devils_advocate._MAX_PER_CYCLE
_MAX_ACTIVE_ADJUSTMENTS = 3  # caps what actually gets injected into prompts

_REVIEW_SYSTEM = (
    "Tu es un analyste ADVERSARIAL qui cherche des PATTERNS récurrents dans un "
    "LOT de 10 trades RÉELLEMENT perdants d'ARIA (jamais un seul trade isolé -- "
    "l'objectif explicite est d'éviter qu'une simple malchance ponctuelle ne "
    "soit prise pour un vrai défaut de stratégie). On te montre les 10 thèses "
    "d'entrée et ce qui s'est réellement passé pour chacune.\n"
    "Cherche un dénominateur commun RÉEL et RÉPÉTÉ (ex. même canal de "
    "découverte, même palier de conviction, même chaîne, même régime de "
    "marché à l'entrée, même raison de clôture, même type de setup technique) "
    "-- jamais une explication forcée si les 10 pertes sont hétérogènes sans "
    "vrai fil conducteur : dans ce cas, dis-le honnêtement (pattern_found: "
    "false), un lot bruyant sans dénominateur commun n'est PAS un échec de "
    "l'analyse, c'est le résultat correct.\n"
    "Si un vrai pattern existe, propose UN SEUL ajustement de trajectoire "
    "concret et actionnable (ex. réduire la taille sur tel canal de "
    "découverte, durcir tel seuil, éviter telle combinaison régime/chaîne) -- "
    "jamais une liste de vagues recommandations, jamais un reproche fondé sur "
    "le seul résultat (le marché a le droit de faire perdre une bonne "
    "décision individuelle ; ici on cherche un biais RÉPÉTÉ, structurel).\n"
    "Réponds STRICTEMENT en JSON, rien d'autre : "
    '{"pattern_found": true|false, "pattern_summary": "<description précise '
    'du dénominateur commun si trouvé, sinon chaîne vide>", "adjustment": '
    '"<ajustement de trajectoire concret et actionnable si pattern_found, '
    'sinon chaîne vide>"}.'
)


def trade_loss_batch_review_enabled() -> bool:
    return os.environ.get("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_loss_batch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_number INTEGER NOT NULL UNIQUE,
                reviewed_at TEXT NOT NULL,
                position_ids TEXT NOT NULL,
                pattern_found INTEGER NOT NULL DEFAULT 0,
                pattern_summary TEXT NOT NULL DEFAULT '',
                adjustment TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_loss_batch_member (
                position_id INTEGER PRIMARY KEY,
                batch_number INTEGER NOT NULL
            )
            """
        )
        await db.commit()


def _is_loss(position: dict) -> bool:
    pnl = position.get("pnl_usd")
    return pnl is not None and float(pnl) < 0


def _format_case_for_prompt(position: dict, index: int) -> str:
    pnl_pct = position.get("pnl_pct")
    pnl_usd = position.get("pnl_usd")
    result_line = (
        f"Résultat réel : {pnl_pct:+.1f}% ({pnl_usd:+.0f}$)"
        if pnl_pct is not None and pnl_usd is not None
        else "Résultat réel : inconnu"
    )
    return "\n".join([
        f"--- Trade perdant #{index} ---",
        f"Contrat/symbole : {position.get('symbol') or position.get('contract') or '?'}",
        f"Chaîne : {position.get('chain') or 'inconnue'} · "
        f"Canal de découverte : {position.get('discovery_channel') or 'inconnu'} · "
        f"Palier de conviction : {position.get('conviction_tier') or 'inconnu'} · "
        f"Régime à l'entrée : {position.get('entry_regime') or 'inconnu'} · "
        f"Stratégie : {position.get('strategy') or 'inconnue'}",
        f"Thèse d'entrée : {position.get('thesis') or '(absente)'}",
        result_line,
        f"Raison de clôture : {position.get('close_reason') or '(inconnue)'}",
        f"Notes de clôture : {position.get('close_notes') or '(aucune)'}",
    ])


def _format_batch_for_prompt(positions: list[dict]) -> str:
    return "\n\n".join(_format_case_for_prompt(p, i + 1) for i, p in enumerate(positions))


async def _review_batch(positions: list[dict], batch_number: int, *, llm) -> dict:
    await _ensure_tables()
    prompt = _format_batch_for_prompt(positions)
    # Same choice as trade_devils_advocate.py -- a model from a different lab
    # than the one that made the trading decisions, never the same one judging
    # itself.
    #
    # 02/08 -- fallback deliberately NOT Claude (see trade_devils_advocate.py's
    # own comment on the identical fallback change, same reasoning: never let
    # the judge's fallback quietly become the future decider's own lab).
    raw = await llm(
        prompt, _REVIEW_SYSTEM, max_tokens=700, temperature=0.0,
        provider="openrouter", model="deepseek/deepseek-r1",
        fallback_provider="openrouter", fallback_model="meta-llama/llama-3.3-70b-instruct",
    )

    pattern_found = False
    pattern_summary = ""
    adjustment = ""
    if raw:
        try:
            data = json.loads(raw)
            pattern_found = bool(data.get("pattern_found", False))
            pattern_summary = str(data.get("pattern_summary", "")).strip()
            adjustment = str(data.get("adjustment", "")).strip()
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            pattern_found, pattern_summary, adjustment = False, "", ""

    # One-way: a confirmed pattern with a real adjustment is promoted
    # immediately into the active set (never a need to wait for a second
    # batch to confirm the same thing twice).
    active = 1 if (pattern_found and adjustment) else 0

    position_ids = [p["id"] for p in positions]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trade_loss_batch "
            "(batch_number, reviewed_at, position_ids, pattern_found, pattern_summary, adjustment, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                batch_number, _now(), json.dumps(position_ids),
                int(pattern_found), pattern_summary, adjustment, active,
            ),
        )
        await db.executemany(
            "INSERT OR IGNORE INTO trade_loss_batch_member (position_id, batch_number) VALUES (?, ?)",
            [(pid, batch_number) for pid in position_ids],
        )
        await db.commit()

    return {
        "batch_number": batch_number, "position_ids": position_ids,
        "pattern_found": pattern_found, "pattern_summary": pattern_summary,
        "adjustment": adjustment,
    }


async def run_trade_loss_batch_review_cycle(*, llm=None, positions_fetch=None) -> dict:
    """One round: accumulates closed LOSING positions never yet batched
    (deduplicated by ``position_id``, oldest-first so batches reflect the real
    chronological order losses happened in), and reviews ONE full batch of 10
    per cycle (never more -- LLM-cost sanity cap, catches up over subsequent
    cycles if a backlog exists). Fail-closed if disabled/paused."""
    if not trade_loss_batch_review_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_tables()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    if positions_fetch is None:
        from aria_core.paper_trader import get_closed_positions as positions_fetch

    closed = await positions_fetch()

    async with aiosqlite.connect(DB_PATH) as db:
        already = {
            row[0]
            for row in await (
                await db.execute("SELECT position_id FROM trade_loss_batch_member")
            ).fetchall()
        }
        row = await (
            await db.execute("SELECT COALESCE(MAX(batch_number), 0) FROM trade_loss_batch")
        ).fetchone()
        next_batch_number = int(row[0]) + 1

    # get_closed_positions returns most-recent-first; process the real
    # chronological order (oldest un-reviewed loss first) so batch #1 is
    # genuinely the first 10 losses ARIA ever made, not an arbitrary mix.
    losses = [p for p in closed if _is_loss(p) and p["id"] not in already]
    losses.sort(key=lambda p: (p.get("closed_at") or "", p["id"]))

    if len(losses) < BATCH_SIZE:
        return {"outcome": "accumulating", "pending": len(losses), "needed": BATCH_SIZE - len(losses)}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    results = []
    batch_number = next_batch_number
    remaining = losses
    for _ in range(_MAX_BATCHES_PER_CYCLE):
        if len(remaining) < BATCH_SIZE:
            break
        batch, remaining = remaining[:BATCH_SIZE], remaining[BATCH_SIZE:]
        try:
            result = await _review_batch(batch, batch_number, llm=llm)
        except Exception as exc:  # noqa: BLE001 -- a failed batch never breaks the cycle
            logger.warning("trade_loss_batch_review: failure on batch %s -- %s", batch_number, exc)
            result = {"batch_number": batch_number, "error": str(exc)[:200]}
        results.append(result)
        batch_number += 1

    patterns_found = sum(1 for r in results if r.get("pattern_found"))
    return {
        "outcome": "ok", "checked": len(closed), "batches_reviewed": len(results),
        "patterns_found": patterns_found, "still_pending": len(remaining), "results": results,
    }


async def active_trajectory_adjustments(limit: int = _MAX_ACTIVE_ADJUSTMENTS) -> list[dict]:
    """The most recent active trajectory adjustments -- never deleted from the
    table, just capped here for what actually gets injected into a prompt
    (one-way: an adjustment older than the cap stays in the DB forever, only
    removed from the ACTIVE set)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT batch_number, pattern_summary, adjustment FROM trade_loss_batch "
            "WHERE active = 1 ORDER BY batch_number DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


def format_trajectory_line(adjustments: list[dict]) -> str:
    """Short, capped line -- same brevity discipline as
    ``trade_devils_advocate.format_lessons_line``: this security guard remains
    latency-critical, never a long history unrolled on every decision."""
    if not adjustments:
        return ""
    parts = [a["adjustment"] for a in adjustments if a.get("adjustment")]
    if not parts:
        return ""
    joined = " | ".join(parts)
    if len(joined) > 400:
        joined = joined[:400].rstrip() + "…"
    return f"Ajustements de trajectoire (patterns confirmés sur des lots de {BATCH_SIZE} pertes) : {joined}"


def format_batch_alert(result: dict) -> str:
    """Telegram-friendly rendering for one reviewed batch."""
    lines = [
        f"🔎 Lot de {BATCH_SIZE} trades perdants analysé (lot n°{result.get('batch_number')})",
    ]
    if result.get("pattern_found"):
        lines.append(f"Pattern confirmé : {result.get('pattern_summary', '')}")
        lines.append(f"Ajustement de trajectoire : {result.get('adjustment', '')}")
    else:
        lines.append("Aucun dénominateur commun réel trouvé sur ce lot -- pertes hétérogènes, pas de pattern forcé.")
    return "\n".join(lines)
