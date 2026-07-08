"""Projets d'apprentissage long-cours — une compétence approfondie par projet, construite
sur PLUSIEURS jours (3 à SKILL_PROJECT_MAX_DAYS), un increment réel par cycle heartbeat,
puis une synthèse finale soumise à l'opérateur. Décision opérateur (08/07) : « si un jour
il lui faut 7 jours pour apprendre une compétence et l'écrire elle-même pour te la
soumettre, pas de problème ».

Toujours 100% analyse/écriture — jamais une action financière, jamais un changement de
code ou de garde-fou (aucun rapport avec `develop_repertoire`/`github_sandbox`, qui restent
les seuls seams touchant le code, eux-mêmes opérateur-gatés). Le sujet de chaque projet
vient du curriculum trading existant (`knowledge/trading_curriculum.yaml`, même source que
l'examen) — jamais un sujet inventé au hasard. Fail-closed : sans LLM configuré, aucun
increment n'est généré ni inventé ; un projet reste simplement en attente du prochain cycle.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

SKILL_PROJECT_MIN_DAYS = 3
SKILL_PROJECT_MAX_DAYS = 7

_INCREMENT_SYSTEM = (
    "Tu es ARIA, en projet d'apprentissage approfondi sur PLUSIEURS jours. Tu écris "
    "aujourd'hui UN nouveau morceau de recherche/analyse sur le concept donné, en tenant "
    "compte de ce que tu as déjà écrit les jours précédents (ne répète pas, avance). Si le "
    "concept est contesté (Smart Money Concepts, ICT...), dis-le explicitement plutôt que "
    "de le présenter comme une vérité de marché prouvée. Factuelle, dense, 4-8 phrases."
)
_FINAL_SYSTEM = (
    "Tu es ARIA. Synthétise en un mémo final cohérent (pas un simple collage) tout ce que "
    "tu as appris sur ce concept au fil du projet — ce que tu retiens, comment l'appliquer, "
    "les limites/incertitudes qui restent. Nuance sur les cadres contestés. 8-15 phrases."
)


@dataclass
class SkillProject:
    id: str
    concept_id: str
    concept_label: str
    category: str
    target_days: int
    day_count: int
    status: str
    started_at: str
    completed_at: str | None = None
    final_writeup: str | None = None


def skill_projects_enabled() -> bool:
    """Gate simple : ON par défaut (même famille que exposure_curriculum/
    cultivation_curriculum — 100% analyse, aucun risque financier). Coupable via
    ARIA_SKILL_PROJECTS_ENABLED=0 si l'opérateur veut réduire le volume d'appels LLM."""
    import os

    raw = os.environ.get("ARIA_SKILL_PROJECTS_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_project (
                id TEXT PRIMARY KEY,
                concept_id TEXT NOT NULL,
                concept_label TEXT NOT NULL,
                category TEXT NOT NULL,
                target_days INTEGER NOT NULL,
                day_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                final_writeup TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_project_increment (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                day_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def _row_to_project(row: tuple) -> SkillProject:
    return SkillProject(
        id=row[0], concept_id=row[1], concept_label=row[2], category=row[3],
        target_days=row[4], day_count=row[5], status=row[6], started_at=row[7],
        completed_at=row[8], final_writeup=row[9],
    )


_PROJECT_COLS = (
    "id", "concept_id", "concept_label", "category", "target_days", "day_count",
    "status", "started_at", "completed_at", "final_writeup",
)


async def _open_project(db: aiosqlite.Connection) -> SkillProject | None:
    cols = ", ".join(_PROJECT_COLS)
    cursor = await db.execute(f"SELECT {cols} FROM skill_project WHERE status = 'open' LIMIT 1")
    row = await cursor.fetchone()
    return _row_to_project(row) if row else None


async def _used_concept_ids(db: aiosqlite.Connection) -> set[str]:
    cursor = await db.execute("SELECT DISTINCT concept_id FROM skill_project")
    rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def _start_new_project(db: aiosqlite.Connection, *, concepts: list[dict] | None = None) -> SkillProject | None:
    """Choisit le prochain concept non encore couvert dans le curriculum trading existant.
    Boucle proprement (reprend depuis le début) une fois tous les concepts couverts —
    jamais un sujet inventé hors curriculum."""
    if concepts is None:
        from aria_core.exam import all_concepts

        concepts = all_concepts()
    if not concepts:
        return None

    used = await _used_concept_ids(db)
    candidates = [c for c in concepts if c["id"] not in used] or concepts  # boucle si tout couvert
    concept = random.choice(candidates)

    project = SkillProject(
        id=str(uuid4()), concept_id=concept["id"], concept_label=concept["label"],
        category=concept.get("category", ""), target_days=random.randint(SKILL_PROJECT_MIN_DAYS, SKILL_PROJECT_MAX_DAYS),
        day_count=0, status="open", started_at=datetime.now(timezone.utc).isoformat(),
    )
    await db.execute(
        "INSERT INTO skill_project (id, concept_id, concept_label, category, target_days, "
        "day_count, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project.id, project.concept_id, project.concept_label, project.category,
         project.target_days, project.day_count, project.status, project.started_at),
    )
    await db.commit()
    return project


async def _increments_for(db: aiosqlite.Connection, project_id: str) -> list[str]:
    cursor = await db.execute(
        "SELECT content FROM skill_project_increment WHERE project_id = ? ORDER BY day_index",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def run_skill_project_cycle(*, llm=None, notifier=None) -> dict:
    """Un tour de projet d'apprentissage long-cours : un increment réel aujourd'hui sur le
    projet ouvert (ou en démarre un nouveau s'il n'y en a pas). Finalise et notifie
    l'opérateur seulement à la fin du projet (jamais un spam quotidien) — la soumission
    finale EST le livrable demandé."""
    await _ensure_tables()
    if llm is None:
        from aria_core.llm import chat_with_context as llm

    async with aiosqlite.connect(DB_PATH) as db:
        project = await _open_project(db)
        if project is None:
            project = await _start_new_project(db)
            if project is None:
                return {"outcome": "no_curriculum"}

        prior = await _increments_for(db, project.id)

    prior_text = "\n\n".join(f"Jour {i + 1} : {text}" for i, text in enumerate(prior)) or "(aucun jour précédent)"
    prompt = (
        f"Concept du projet : {project.concept_label} (catégorie : {project.category})\n"
        f"Jour {project.day_count + 1} sur {project.target_days}.\n\n"
        f"Ce que tu as déjà écrit :\n{prior_text}\n\n"
        "Écris le morceau d'aujourd'hui."
    )
    increment_text = await llm(prompt, _INCREMENT_SYSTEM, max_tokens=500)
    if not increment_text or not increment_text.strip():
        logger.info("skill_projects: increment échoué pour %s — cycle ignoré", project.concept_id)
        return {"outcome": "increment_failed", "project_id": project.id}
    increment_text = increment_text.strip()

    new_day_count = project.day_count + 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO skill_project_increment (id, project_id, day_index, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), project.id, new_day_count, increment_text, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "UPDATE skill_project SET day_count = ? WHERE id = ?", (new_day_count, project.id),
        )
        await db.commit()

    if new_day_count < project.target_days:
        return {"outcome": "increment", "project_id": project.id, "day": new_day_count, "target_days": project.target_days}

    # Dernier jour : synthese finale + soumission a l'operateur.
    all_increments = prior + [increment_text]
    final_prompt = (
        f"Concept : {project.concept_label} (catégorie : {project.category})\n\n"
        + "\n\n".join(f"Jour {i + 1} : {t}" for i, t in enumerate(all_increments))
    )
    writeup = await llm(final_prompt, _FINAL_SYSTEM, max_tokens=900)
    writeup = (writeup or "").strip() or (
        "Synthèse finale indisponible (LLM) — voir les increments jour par jour ci-dessus."
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE skill_project SET status = 'completed', completed_at = ?, final_writeup = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), writeup, project.id),
        )
        await db.commit()

    if notifier:
        try:
            await notifier(
                f"📖 Projet d'apprentissage terminé ({project.target_days} jours) — "
                f"{project.concept_label}\n\n{writeup}"
            )
        except Exception:  # noqa: BLE001 — une notification ratée ne doit pas annuler la finalisation
            pass

    return {
        "outcome": "completed", "project_id": project.id, "concept_label": project.concept_label,
        "target_days": project.target_days, "writeup": writeup,
    }


async def projects_status() -> dict:
    """Vue d'ensemble (public-safe, agrégée) : projet en cours + historique completé."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        open_project = await _open_project(db)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM skill_project WHERE status = 'completed'"
        )
        completed_count = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT concept_label, completed_at FROM skill_project WHERE status = 'completed' "
            "ORDER BY completed_at DESC LIMIT 1"
        )
        last_row = await cursor.fetchone()

    return {
        "enabled": skill_projects_enabled(),
        "open_project": (
            {
                "concept_label": open_project.concept_label, "day": open_project.day_count,
                "target_days": open_project.target_days,
            }
            if open_project else None
        ),
        "completed_count": completed_count,
        "last_completed": (
            {"concept_label": last_row[0], "completed_at": last_row[1]} if last_row else None
        ),
    }
