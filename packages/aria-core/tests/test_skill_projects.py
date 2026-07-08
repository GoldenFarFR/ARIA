"""Projets d'apprentissage long-cours — increments quotidiens, finalisation, soumission
(hors-ligne, tout injecté)."""
from __future__ import annotations

import pytest

from aria_core.knowledge import skill_projects as sp

_CONCEPTS = [
    {"id": "concept_a", "label": "Order blocks", "category": "smc"},
    {"id": "concept_b", "label": "RSI divergence", "category": "quant"},
]


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "skill_projects_test.db"))
    yield


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SKILL_PROJECTS_ENABLED", raising=False)
    assert sp.skill_projects_enabled() is True


def test_disabled_via_explicit_flag(monkeypatch):
    monkeypatch.setenv("ARIA_SKILL_PROJECTS_ENABLED", "0")
    assert sp.skill_projects_enabled() is False


async def _fake_llm_factory():
    calls = {"n": 0}

    async def llm(prompt, system, max_tokens=500):
        calls["n"] += 1
        return f"Increment factice #{calls['n']} — {prompt[:20]}"

    return llm, calls


@pytest.mark.asyncio
async def test_first_cycle_starts_a_project_from_curriculum():
    async def llm(prompt, system, max_tokens=500):
        return "Premier increment factuel."

    result = await sp.run_skill_project_cycle(llm=llm)
    assert result["outcome"] == "increment"
    assert result["day"] == 1

    status = await sp.projects_status()
    assert status["open_project"]["day"] == 1
    assert status["open_project"]["concept_label"] in {"Order blocks", "RSI divergence"} or status["open_project"]["concept_label"]


@pytest.mark.asyncio
async def test_project_accumulates_and_finalizes_on_last_day(monkeypatch):
    monkeypatch.setattr(sp, "SKILL_PROJECT_MIN_DAYS", 2)
    monkeypatch.setattr(sp, "SKILL_PROJECT_MAX_DAYS", 2)  # deterministe : exactement 2 jours

    seen_prompts = []

    async def llm(prompt, system, max_tokens=500):
        seen_prompts.append(prompt)
        if "Synthétise" in system or len(seen_prompts) > 2:
            return "Synthèse finale ancrée sur les 2 jours."
        return f"Increment jour {len(seen_prompts)}."

    day1 = await sp.run_skill_project_cycle(llm=llm)
    assert day1["outcome"] == "increment"
    assert day1["day"] == 1
    assert day1["target_days"] == 2

    day2 = await sp.run_skill_project_cycle(llm=llm)
    assert day2["outcome"] == "completed"
    assert "Synthèse finale" in day2["writeup"]

    status = await sp.projects_status()
    assert status["open_project"] is None  # le projet est termine, plus rien d'ouvert
    assert status["completed_count"] == 1
    assert status["last_completed"] is not None


@pytest.mark.asyncio
async def test_second_project_avoids_repeating_a_recently_used_concept():
    """Pool de 2 concepts : une fois le premier couvert, le second projet doit prendre
    l'AUTRE concept, pas repeter le meme au hasard."""
    import aiosqlite

    await sp._ensure_tables()
    async with aiosqlite.connect(sp.DB_PATH) as db:
        p1 = await sp._start_new_project(db, concepts=_CONCEPTS)
        await db.execute(
            "UPDATE skill_project SET status = 'completed' WHERE id = ?", (p1.id,),
        )
        await db.commit()

    async with aiosqlite.connect(sp.DB_PATH) as db:
        p2 = await sp._start_new_project(db, concepts=_CONCEPTS)

    assert p2.concept_id != p1.concept_id  # le seul autre concept du pool de 2


@pytest.mark.asyncio
async def test_increment_failure_does_not_crash_or_advance_day():
    async def broken_llm(prompt, system, max_tokens=500):
        return None

    result = await sp.run_skill_project_cycle(llm=broken_llm)
    assert result["outcome"] == "increment_failed"

    status = await sp.projects_status()
    # Le projet est cree (il faut bien un sujet a retenter) mais day=0 : aucun increment
    # invente, jamais une avancee sans contenu reel.
    assert status["open_project"]["day"] == 0


@pytest.mark.asyncio
async def test_final_writeup_notifies_operator():
    import aiosqlite

    await sp._ensure_tables()
    async with aiosqlite.connect(sp.DB_PATH) as db:
        project = await sp._start_new_project(db, concepts=[_CONCEPTS[0]])
        await db.execute(
            "UPDATE skill_project SET target_days = 1 WHERE id = ?", (project.id,),
        )
        await db.commit()

    notified = []

    async def notifier(text):
        notified.append(text)

    async def llm(prompt, system, max_tokens=500):
        return "Synthèse finale du concept." if "Synthétise" in system else "Increment du jour."

    result = await sp.run_skill_project_cycle(llm=llm, notifier=notifier)
    assert result["outcome"] == "completed"
    assert len(notified) == 1
    assert "Synthèse finale" in notified[0] or "terminé" in notified[0].lower()
