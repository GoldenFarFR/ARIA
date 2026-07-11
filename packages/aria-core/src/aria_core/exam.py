"""Examen ARIA — génère des questions de trading (knowledge/trading_curriculum.yaml), les
pose au vrai moteur de raisonnement d'ARIA, note les réponses via un juge LLM dédié.

Rehearsal pédagogique en parallèle du paper-trading (20 jours, décision opérateur 08/07) —
jamais une décision de trading, jamais une action financière : uniquement mesurer et
consigner la qualité du raisonnement. Fail-closed partout : sans LLM configuré ou sans
curriculum, rien n'est généré ni inventé.
"""
from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite
import yaml

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
_CURRICULUM_PATH = Path(__file__).parent / "knowledge" / "trading_curriculum.yaml"

# Duree du programme (decision operateur 08/07, en parallele du paper-trading 20 jours).
EXAM_PROGRAM_DAYS = 20

_QUESTION_SYSTEM = "Tu rédiges des questions d'examen de trading factuelles et rigoureuses."
_ANSWER_SYSTEM = (
    "Tu es ARIA. Réponds en experte de trading, factuelle et nuancée. Si un concept est "
    "contesté (Smart Money Concepts, ICT...), dis-le explicitement plutôt que de le "
    "présenter comme une vérité de marché prouvée."
)
_JUDGE_SYSTEM = (
    "Tu es un examinateur de desk de trading, exigeant mais juste. Note la réponse suivante "
    "de 0 à 10 (0 = faux/hors sujet, 10 = réponse experte et nuancée). Si le concept testé est "
    "contesté (Smart Money Concepts, ICT...), une bonne réponse le signale sans le présenter "
    'comme une vérité prouvée. Réponds STRICTEMENT en JSON : {"score": <0-10>, "notes": "<2-3 phrases>"}'
)


def exam_enabled() -> bool:
    """Seam gaté OFF par défaut. Aucune question générée, aucun appel LLM sans ce flag."""
    return os.environ.get("ARIA_EXAM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def load_curriculum() -> list[dict]:
    """Curriculum (catégories -> concepts). Dégradation gracieuse : liste vide si absent/invalide."""
    try:
        raw = yaml.safe_load(_CURRICULUM_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, yaml.YAMLError) as exc:
        logger.info("exam: curriculum illisible (%s)", exc)
        return []


def all_concepts() -> list[dict]:
    """Aplatit le curriculum en une liste de concepts, chacun annoté de sa catégorie."""
    out: list[dict] = []
    for cat in load_curriculum():
        for c in cat.get("concepts", []) or []:
            out.append({**c, "category": cat.get("category", ""), "category_label": cat.get("label", "")})
    return out


async def init_exam_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS exam_question (
                id TEXT PRIMARY KEY,
                day INTEGER NOT NULL,
                concept_id TEXT NOT NULL,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                created_at TEXT NOT NULL,
                cycle INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        # Migration à chaud : ajoute `cycle` aux DB existantes (SQLite ne le crée pas
        # si la table préexiste). Idempotent, non destructif — cf. vc_predictions.py.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(exam_question)")).fetchall()
        }
        if "cycle" not in existing:
            await db.execute(
                "ALTER TABLE exam_question ADD COLUMN cycle INTEGER NOT NULL DEFAULT 1"
            )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS exam_answer (
                question_id TEXT PRIMARY KEY,
                answer TEXT NOT NULL,
                score REAL,
                judge_notes TEXT,
                answered_at TEXT NOT NULL,
                judged_at TEXT
            )
            """
        )
        await db.commit()


@dataclass
class ExamQuestion:
    id: str
    day: int
    concept_id: str
    category: str
    question: str


async def _cycle_state(db: aiosqlite.Connection) -> tuple[int, set[str]]:
    """Cycle courant + concepts déjà posés dans ce cycle (persisté dans exam_question.cycle).

    Un cycle regroupe les jours tant que le pool de concepts n'est pas épuisé. Table
    vide -> cycle 1, aucun concept encore posé."""
    row = await (await db.execute("SELECT MAX(cycle) FROM exam_question")).fetchone()
    cycle = row[0] or 1
    cursor = await db.execute(
        "SELECT DISTINCT concept_id FROM exam_question WHERE cycle = ?", (cycle,)
    )
    asked_ids = {r[0] for r in await cursor.fetchall()}
    return cycle, asked_ids


def _select_concepts_for_day(
    concepts: list[dict], n: int, asked_ids: set[str], cycle: int
) -> tuple[list[dict], int]:
    """Choisit jusqu'à ``n`` concepts sans jamais reproposer un concept déjà posé dans le
    cycle courant. Si le pool restant du cycle ne suffit pas, épuise-le puis complète en
    démarrant le cycle suivant (jamais deux fois le même concept le même jour).

    Retourne les concepts choisis et le numéro de cycle à leur associer."""
    remaining = [c for c in concepts if c["id"] not in asked_ids]
    if len(remaining) >= n:
        picked = remaining if len(remaining) == n else random.sample(remaining, n)
        return picked, cycle

    # Pool du cycle courant épuisé (ou insuffisant) : on prend le reste, puis on
    # démarre un nouveau cycle pour compléter la journée.
    picked = remaining[:]
    picked_ids = {c["id"] for c in picked}
    fresh_pool = [c for c in concepts if c["id"] not in picked_ids]
    need = n - len(picked)
    if fresh_pool:
        picked += fresh_pool if len(fresh_pool) <= need else random.sample(fresh_pool, need)
    return picked, cycle + 1


async def current_exam_cycle() -> int:
    """Numéro du cycle courant (1-indexé) — combien de fois le pool des 67 concepts a
    déjà été intégralement parcouru (+1 pour le cycle en cours)."""
    await init_exam_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cycle, _ = await _cycle_state(db)
    return cycle


async def generate_daily_questions(day: int, n: int = 25, *, llm=None) -> list[ExamQuestion]:
    """Génère jusqu'à ``n`` questions pour ``day``, une par concept tiré sans remise dans
    le curriculum, sans jamais reproposer un concept déjà posé au cours du cycle courant
    (suivi cross-jour persisté via ``exam_question.cycle`` — le pool de 67 concepts doit
    être intégralement épuisé avant qu'un concept ne revienne). Fail-closed : liste vide
    si le curriculum est vide — jamais une question inventée sans base conceptuelle. Une
    génération LLM individuelle qui échoue est ignorée (pas de question vide insérée),
    les autres continuent."""
    concepts = all_concepts()
    if not concepts:
        return []

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    await init_exam_db()
    questions: list[ExamQuestion] = []
    async with aiosqlite.connect(DB_PATH) as db:
        cycle, asked_ids = await _cycle_state(db)
        picked, day_cycle = _select_concepts_for_day(concepts, n, asked_ids, cycle)
        for c in picked:
            prompt = (
                "Rédige UNE question d'examen de trading (niveau exigeant, entretien "
                "d'embauche desk) qui teste la compréhension du concept suivant — sans "
                "jamais présenter le concept comme une vérité de marché prouvée, seulement "
                "comme un cadre d'analyse à expliquer/appliquer.\n\n"
                f"Concept : {c['label']}\nNote : {c.get('note', '')}\n\n"
                "Réponds UNIQUEMENT avec la question, sans préambule."
            )
            text = await llm(prompt, _QUESTION_SYSTEM, max_tokens=200)
            if not text or not text.strip():
                logger.info("exam: génération échouée pour le concept %s — ignoré", c["id"])
                continue
            q = ExamQuestion(
                id=str(uuid4()), day=day, concept_id=c["id"], category=c["category"],
                question=text.strip(),
            )
            await db.execute(
                "INSERT INTO exam_question "
                "(id, day, concept_id, category, question, created_at, cycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (q.id, q.day, q.concept_id, q.category, q.question,
                 datetime.now(timezone.utc).isoformat(), day_cycle),
            )
            questions.append(q)
        await db.commit()
    return questions


def _parse_judge(raw: str | None) -> tuple[float | None, str]:
    if not raw:
        return None, "jugement indisponible"
    try:
        data = json.loads(raw)
        score = max(0.0, min(10.0, float(data.get("score"))))
        return score, str(data.get("notes", ""))[:500]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, "jugement non parsable"


async def administer_question(q: ExamQuestion, *, answerer=None, judge=None) -> dict:
    """Pose la question au moteur de raisonnement d'ARIA, note la réponse via le juge.

    Ne déclenche jamais d'action financière — uniquement mesurer et consigner. Un
    jugement non parsable donne ``score=None`` (exclu des moyennes), jamais un score
    inventé."""
    if answerer is None:
        from aria_core.llm import chat_with_context as answerer
    if judge is None:
        from aria_core.llm import chat_with_context as judge

    answer = await answerer(q.question, _ANSWER_SYSTEM, max_tokens=400)
    answer = (answer or "").strip() or "Pas de réponse générée."

    judge_raw = await judge(
        f"Question : {q.question}\n\nRéponse d'ARIA : {answer}", _JUDGE_SYSTEM, max_tokens=250,
    )
    score, notes = _parse_judge(judge_raw)

    now = datetime.now(timezone.utc).isoformat()
    await init_exam_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO exam_answer "
            "(question_id, answer, score, judge_notes, answered_at, judged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (q.id, answer, score, notes, now, now if score is not None else None),
        )
        await db.commit()
    return {"question_id": q.id, "answer": answer, "score": score, "notes": notes}


async def current_exam_day() -> int:
    """Jour courant du programme (1-indexé). Un nouveau jour ne démarre qu'au cycle
    heartbeat suivant (cadence quotidienne) — jamais de jour inventé en avance."""
    await init_exam_db()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT MAX(day) FROM exam_question")).fetchone()
    return (row[0] or 0) + 1


async def daily_summary(day: int) -> dict:
    await init_exam_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT a.score FROM exam_answer a JOIN exam_question q ON a.question_id = q.id "
            "WHERE q.day = ? AND a.score IS NOT NULL",
            (day,),
        )
        scores = [row[0] for row in await cursor.fetchall()]
    if not scores:
        return {"day": day, "answered": 0, "avg_score": None}
    return {"day": day, "answered": len(scores), "avg_score": round(sum(scores) / len(scores), 2)}


async def cumulative_summary() -> dict:
    await init_exam_db()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT COUNT(*), AVG(score) FROM exam_answer WHERE score IS NOT NULL")
        ).fetchone()
        total_questions = (await (await db.execute("SELECT COUNT(*) FROM exam_question")).fetchone())[0]
    answered, avg = (row[0] or 0), row[1]
    return {
        "total_questions": total_questions,
        "answered": answered,
        "avg_score": round(avg, 2) if avg is not None else None,
    }
