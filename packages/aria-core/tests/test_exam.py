"""Examen ARIA — génération de questions, administration, notation (hors-ligne, injecté)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from aria_core import exam


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(exam, "DB_PATH", str(tmp_path / "exam_test.db"))
    yield


async def _insert_question(q: exam.ExamQuestion) -> None:
    """Insère directement une question déjà construite (contourne generate_daily_questions
    pour les tests qui portent sur administer_question/daily_summary uniquement)."""
    await exam.init_exam_db()
    async with aiosqlite.connect(exam.DB_PATH) as db:
        await db.execute(
            "INSERT INTO exam_question (id, day, concept_id, category, question, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (q.id, q.day, q.concept_id, q.category, q.question,
             datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


def test_curriculum_loads_and_flattens():
    concepts = exam.all_concepts()
    assert len(concepts) >= 40  # 5 catégories x 10 concepts
    assert all({"id", "label", "category"} <= set(c) for c in concepts)


def test_exam_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_EXAM_ENABLED", raising=False)
    assert exam.exam_enabled() is False


@pytest.mark.asyncio
async def test_generate_daily_questions_stores_rows():
    async def fake_llm(prompt, system, max_tokens=200):
        return "Question factice sur ce concept ?"

    questions = await exam.generate_daily_questions(day=1, n=5, llm=fake_llm)
    assert len(questions) == 5
    assert all(q.day == 1 and q.question for q in questions)
    assert len({q.concept_id for q in questions}) == 5  # pas de doublon de concept


@pytest.mark.asyncio
async def test_generate_daily_questions_skips_failed_generation():
    calls = {"n": 0}

    async def flaky_llm(prompt, system, max_tokens=200):
        calls["n"] += 1
        return None if calls["n"] % 2 == 0 else "Une vraie question ?"

    questions = await exam.generate_daily_questions(day=1, n=4, llm=flaky_llm)
    assert len(questions) == 2  # la moitié a échoué -> ignorée, jamais une question vide
    assert all(q.question for q in questions)


@pytest.mark.asyncio
async def test_current_exam_day_progresses_with_questions():
    assert await exam.current_exam_day() == 1  # rien encore genere -> jour 1

    async def fake_llm(prompt, system, max_tokens=200):
        return "Question ?"

    await exam.generate_daily_questions(day=1, n=2, llm=fake_llm)
    assert await exam.current_exam_day() == 2

    await exam.generate_daily_questions(day=2, n=2, llm=fake_llm)
    assert await exam.current_exam_day() == 3


@pytest.mark.asyncio
async def test_administer_question_scores_and_stores():
    q = exam.ExamQuestion(id="q1", day=1, concept_id="hammer", category="candlesticks_and_basics",
                           question="Décris un hammer.")
    await _insert_question(q)

    async def fake_answerer(prompt, system, max_tokens=400):
        return "Un hammer est un pattern de retournement haussier avec une longue mèche basse."

    async def fake_judge(prompt, system, max_tokens=250):
        return json.dumps({"score": 8, "notes": "Réponse correcte et concise."})

    result = await exam.administer_question(q, answerer=fake_answerer, judge=fake_judge)
    assert result["score"] == 8.0
    assert "correcte" in result["notes"]

    summary = await exam.daily_summary(1)
    assert summary == {"day": 1, "answered": 1, "avg_score": 8.0}


@pytest.mark.asyncio
async def test_administer_question_handles_unparsable_judge():
    q = exam.ExamQuestion(id="q2", day=1, concept_id="doji", category="candlesticks_and_basics",
                           question="Décris un doji.")

    async def fake_answerer(prompt, system, max_tokens=400):
        return "Un doji signale l'indécision."

    async def broken_judge(prompt, system, max_tokens=250):
        return "pas du JSON valide"

    result = await exam.administer_question(q, answerer=fake_answerer, judge=broken_judge)
    assert result["score"] is None  # jamais un score inventé
    assert "non parsable" in result["notes"]


@pytest.mark.asyncio
async def test_judge_score_clamped_to_0_10():
    q = exam.ExamQuestion(id="q3", day=1, concept_id="rsi_basics", category="candlesticks_and_basics",
                           question="Explique le RSI.")

    async def fake_answerer(prompt, system, max_tokens=400):
        return "réponse"

    async def overshooting_judge(prompt, system, max_tokens=250):
        return json.dumps({"score": 15, "notes": "trop généreux"})

    result = await exam.administer_question(q, answerer=fake_answerer, judge=overshooting_judge)
    assert result["score"] == 10.0


@pytest.mark.asyncio
async def test_cumulative_summary_excludes_unscored():
    q1 = exam.ExamQuestion(id="q4", day=1, concept_id="doji", category="c", question="Q1?")
    q2 = exam.ExamQuestion(id="q5", day=1, concept_id="hammer", category="c", question="Q2?")

    async def answerer(prompt, system, max_tokens=400):
        return "réponse"

    async def judge_ok(prompt, system, max_tokens=250):
        return json.dumps({"score": 6, "notes": "ok"})

    async def judge_broken(prompt, system, max_tokens=250):
        return None

    await exam.administer_question(q1, answerer=answerer, judge=judge_ok)
    await exam.administer_question(q2, answerer=answerer, judge=judge_broken)

    cumulative = await exam.cumulative_summary()
    assert cumulative["answered"] == 1  # q5 (score=None) exclue de la moyenne
    assert cumulative["avg_score"] == 6.0
