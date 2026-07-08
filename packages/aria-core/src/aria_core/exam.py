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
                created_at TEXT NOT NULL
            )
            """
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


async def generate_daily_questions(day: int, n: int = 25, *, llm=None) -> list[ExamQuestion]:
    """Génère jusqu'à ``n`` questions pour ``day``, une par concept tiré sans remise dans
    le curriculum. Fail-closed : liste vide si le curriculum est vide — jamais une question
    inventée sans base conceptuelle. Une génération LLM individuelle qui échoue est
    ignorée (pas de question vide insérée), les autres continuent."""
    concepts = all_concepts()
    if not concepts:
        return []
    picked = concepts[:] if len(concepts) <= n else random.sample(concepts, n)

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    await init_exam_db()
    questions: list[ExamQuestion] = []
    async with aiosqlite.connect(DB_PATH) as db:
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
                "INSERT INTO exam_question (id, day, concept_id, category, question, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (q.id, q.day, q.concept_id, q.category, q.question,
                 datetime.now(timezone.utc).isoformat()),
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
