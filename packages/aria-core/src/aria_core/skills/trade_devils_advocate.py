"""ARIA's Devil — devil's advocate for her own trading decisions (20/07).

Direct follow-up to the thesis ARIA wrote herself, in her own free repo
(aria-brain, chapter 1, "My investment thesis"): she states there that she
wants to identify "the first real error in judgment... not a technical error,
a reasoning error." This module builds exactly that mechanism, never invented
from scratch -- she's the one who validated the need for it, in her own
words.

Same principle as the Devil's Advocate that reviews ARIA's code (script
``devils-advocate-review.sh``, DeepSeek R1 via OpenRouter, never the same
model that wrote the code): a GENUINELY different model reviews every CLOSED
position and judges the DECISION, never the outcome. A loss on an honestly
well-built trade produces NOTHING -- it's not a lesson, it's market noise
(same "process over outcome" doctrine already established for TSG/the risk
management protocol). Only a real REASONING flaw, identifiable with what was
knowable AT THE TIME of entry (never a retrospective fact), produces a
lesson.

One-way (doctrine established with the operator, 20/07, same family as the
trailing stop/breakeven floor/macro regime): a confirmed lesson can ONLY
tighten future caution, never relax it -- no mechanism here removes/softens
an already-written lesson. Persisted in SQLite (never a file committed to the
ARIA repo -- not a new external-write capability, same doctrine as
``momentum_blacklist.py``/``momentum_funnel_log.py``), read back by
``momentum_entry.py`` as a short line injected into the security guard --
never into the pipeline's fastest prompts (``_llm_confirm_and_gate``/
``_llm_security_gate`` remain latency-critical, the injection stays
deliberately short and capped).

Gated OFF by default (``ARIA_TRADE_DEVILS_ADVOCATE_ENABLED``), respects
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

_MAX_PER_CYCLE = 5  # sanity cap (LLM cost), not a risk cap
_MAX_ACTIVE_LESSONS = 3  # caps what actually gets injected into prompts

_REVIEW_SYSTEM = (
    "Tu es un critique ADVERSARIAL des décisions de trading d'ARIA -- un modèle "
    "différent de celui qui a pris la décision, jamais complaisant. On te montre "
    "une position RÉELLEMENT clôturée : sa thèse d'entrée complète (les signaux "
    "qu'ARIA avait à l'époque) et ce qui s'est RÉELLEMENT passé (résultat réel, "
    "jamais inventé). Ta seule question : avec CE QUI ÉTAIT CONNAISSABLE au "
    "moment de l'entrée -- jamais un fait rétrospectif que seul le résultat "
    "révèle -- cette décision était-elle défendable ?\n"
    "Une perte sur un trade honnêtement bien construit N'EST PAS une faille -- "
    "réponds 'sound' dans ce cas, même si le résultat est mauvais : le marché a "
    "le droit de faire perdre une bonne décision, ce n'est pas ça qu'on cherche. "
    "Réponds 'flawed' UNIQUEMENT si tu identifies un vrai angle mort de "
    "RAISONNEMENT qui existait déjà à l'entrée (ex. un signal d'alerte connu à "
    "l'époque mais sous-pondéré, une contradiction interne dans la thèse "
    "elle-même, un chiffre affiché qui ne correspond pas à ce qui était "
    "réellement exécutable). Jamais un reproche fondé uniquement sur le "
    "résultat (\"elle aurait dû savoir que ça allait chuter\" n'est jamais une "
    "réponse valide -- il faut un vrai défaut de raisonnement, pas la chance du "
    "marché).\n"
    "Réponds STRICTEMENT en JSON, rien d'autre : "
    '{"verdict": "sound"|"flawed", "flaw": "<description précise si flawed, '
    'sinon chaîne vide>", "lesson": "<leçon courte et actionnable si flawed, '
    'sinon chaîne vide>"}.'
)


def trade_devils_advocate_enabled() -> bool:
    return os.environ.get("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_devils_advocate_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL UNIQUE,
                contract TEXT,
                symbol TEXT,
                reviewed_at TEXT NOT NULL,
                verdict TEXT NOT NULL,
                flaw TEXT NOT NULL DEFAULT '',
                lesson TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()


def _format_case_for_prompt(position: dict) -> str:
    pnl_pct = position.get("pnl_pct")
    pnl_usd = position.get("pnl_usd")
    result_line = (
        f"Résultat réel : {pnl_pct:+.1f}% ({pnl_usd:+.0f}$)"
        if pnl_pct is not None and pnl_usd is not None
        else "Résultat réel : inconnu"
    )
    return "\n".join([
        f"Thèse d'entrée (déjà journalisée par ARIA, jamais réécrite ici) : "
        f"{position.get('thesis') or '(absente)'}",
        f"Prix d'entrée : {position.get('entry_price')} · "
        f"Prix de sortie : {position.get('exit_price')}",
        result_line,
        f"Raison de clôture : {position.get('close_reason') or '(inconnue)'}",
        f"Notes de clôture : {position.get('close_notes') or '(aucune)'}",
    ])


async def _review_one(position: dict, *, llm) -> dict:
    await _ensure_table()
    prompt = _format_case_for_prompt(position)
    # Same choice as the Devil's Advocate that reviews the code (DeepSeek R1
    # via OpenRouter) -- a model from a different lab than the one that made
    # the decision, never the same one judging itself.
    raw = await llm(
        prompt, _REVIEW_SYSTEM, max_tokens=500, temperature=0.0,
        provider="openrouter", model="deepseek/deepseek-r1",
        fallback_provider="openrouter", fallback_model="anthropic/claude-haiku-4.5",
    )

    verdict = "sound"
    flaw = ""
    lesson = ""
    if raw:
        try:
            data = json.loads(raw)
            verdict = str(data.get("verdict", "sound")).strip().lower()
            if verdict not in ("sound", "flawed"):
                verdict = "sound"
            flaw = str(data.get("flaw", "")).strip()
            lesson = str(data.get("lesson", "")).strip()
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            verdict, flaw, lesson = "sound", "", ""

    # One-way: a "flawed" position with a real lesson is promoted immediately
    # into the active set (never a need to wait for repetition -- an isolated
    # but clear-cut case, like MAGIC, deserves to be seen the first time).
    active = 1 if (verdict == "flawed" and lesson) else 0

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trade_devils_advocate_log "
            "(position_id, contract, symbol, reviewed_at, verdict, flaw, lesson, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                position["id"], position.get("contract", ""), position.get("symbol", ""),
                _now(), verdict, flaw, lesson, active,
            ),
        )
        await db.commit()

    return {
        "position_id": position["id"], "contract": position.get("contract", ""),
        "verdict": verdict, "flaw": flaw, "lesson": lesson,
    }


async def run_trade_devils_advocate_cycle(*, llm=None, positions_fetch=None) -> dict:
    """One round: reviews closed positions never yet examined (deduplicated by
    ``position_id``, same pattern as ``pump_dump_autopsy_log``). Fail-closed
    if disabled/paused. A failure on one case never breaks the others."""
    if not trade_devils_advocate_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_table()

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
                await db.execute("SELECT position_id FROM trade_devils_advocate_log")
            ).fetchall()
        }

    candidates = [p for p in closed if p["id"] not in already][:_MAX_PER_CYCLE]
    if not candidates:
        return {"outcome": "nothing_to_review", "checked": len(closed)}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    results = []
    for position in candidates:
        try:
            result = await _review_one(position, llm=llm)
        except Exception as exc:  # noqa: BLE001 -- a failed review never breaks the cycle
            logger.warning(
                "trade_devils_advocate: failure on position %s -- %s", position["id"], exc,
            )
            result = {"position_id": position["id"], "verdict": "error", "error": str(exc)[:200]}
        results.append(result)

    flawed = sum(1 for r in results if r.get("verdict") == "flawed")
    return {"outcome": "ok", "checked": len(closed), "reviewed": len(candidates), "flawed": flawed, "results": results}


async def active_lessons(limit: int = _MAX_ACTIVE_LESSONS) -> list[dict]:
    """The most recent active lessons -- never deleted from the table, just
    capped here for what actually gets injected into a prompt (one-way: a
    lesson older than the cap stays in the DB forever, only removed from the
    ACTIVE set)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT contract, symbol, flaw, lesson FROM trade_devils_advocate_log "
            "WHERE active = 1 ORDER BY reviewed_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


def format_lessons_line(lessons: list[dict]) -> str:
    """Short, capped line -- same brevity discipline as
    ``momentum_entry._weekly_pacing_line``: these prompts are latency-critical,
    never a long history unrolled on every decision."""
    if not lessons:
        return ""
    parts = [
        f"{(l.get('symbol') or l.get('contract') or '?')[:12]} : {l['lesson']}"
        for l in lessons
        if l.get("lesson")
    ]
    if not parts:
        return ""
    joined = " | ".join(parts)
    if len(joined) > 400:
        joined = joined[:400].rstrip() + "…"
    return f"Leçons apprises de tes propres erreurs de raisonnement passées : {joined}"
