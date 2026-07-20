"""Le Diable d'ARIA (trade_devils_advocate.py, 20/07) — hors-ligne, tout injecté.
Vérifie : le gating, le jugement sur la DÉCISION (jamais le résultat -- un verdict
"sound" n'écrit jamais de leçon active), le dédoublonnage par position, le sens
unique (une leçon confirmée reste en base pour toujours, seule la vue ACTIVE est
plafonnée), et le format court injecté dans les prompts momentum."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import trade_devils_advocate as tda


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tda, "DB_PATH", str(tmp_path / "trade_devils_advocate_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    monkeypatch.delenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", raising=False)
    yield


def _position(id_=1, **overrides):
    base = {
        "id": id_, "contract": "0xabc", "symbol": "TEST", "thesis": "golden pocket + R/R 2.5",
        "entry_price": 1.0, "exit_price": 0.85, "pnl_usd": -1500.0, "pnl_pct": -15.0,
        "close_reason": "stop suiveur", "close_notes": "Stop déclenché après retracement.",
    }
    base.update(overrides)
    return base


def _llm_returning(payload: dict):
    async def _llm(*args, **kwargs):
        return json.dumps(payload)
    return _llm


# ── gate ─────────────────────────────────────────────────────────────────────────

def test_disabled_by_default():
    assert tda.trade_devils_advocate_enabled() is False


def test_enabled_when_set(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")
    assert tda.trade_devils_advocate_enabled() is True


# ── _review_one ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sound_verdict_writes_no_active_lesson():
    llm = _llm_returning({"verdict": "sound", "flaw": "", "lesson": ""})
    result = await tda._review_one(_position(), llm=llm)
    assert result["verdict"] == "sound"
    lessons = await tda.active_lessons()
    assert lessons == []


@pytest.mark.asyncio
async def test_flawed_verdict_with_lesson_is_promoted_active():
    llm = _llm_returning({
        "verdict": "flawed",
        "flaw": "score fondamental 1/10 ignoré, R/R gonflé par l'impact de liquidité",
        "lesson": "vérifier le R/R après impact sur les pools fins",
    })
    result = await tda._review_one(_position(), llm=llm)
    assert result["verdict"] == "flawed"
    lessons = await tda.active_lessons()
    assert len(lessons) == 1
    assert "impact" in lessons[0]["lesson"]


@pytest.mark.asyncio
async def test_flawed_verdict_without_lesson_text_not_promoted():
    """Un verdict flawed sans leçon concrète (LLM incomplet) ne doit jamais polluer
    le jeu actif avec une entrée vide."""
    llm = _llm_returning({"verdict": "flawed", "flaw": "quelque chose", "lesson": ""})
    await tda._review_one(_position(), llm=llm)
    assert await tda.active_lessons() == []


@pytest.mark.asyncio
async def test_unparsable_llm_output_defaults_to_sound():
    async def llm(*args, **kwargs):
        return "pas du JSON du tout"
    result = await tda._review_one(_position(), llm=llm)
    assert result["verdict"] == "sound"


@pytest.mark.asyncio
async def test_invalid_verdict_value_defaults_to_sound():
    llm = _llm_returning({"verdict": "peut-être", "flaw": "", "lesson": ""})
    result = await tda._review_one(_position(), llm=llm)
    assert result["verdict"] == "sound"


@pytest.mark.asyncio
async def test_none_llm_reply_defaults_to_sound():
    async def llm(*args, **kwargs):
        return None
    result = await tda._review_one(_position(), llm=llm)
    assert result["verdict"] == "sound"


# ── run_trade_devils_advocate_cycle ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled():
    result = await tda.run_trade_devils_advocate_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await tda.run_trade_devils_advocate_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_cycle_reviews_only_new_positions(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")

    async def positions_fetch():
        return [_position(id_=1), _position(id_=2)]

    llm = _llm_returning({"verdict": "sound", "flaw": "", "lesson": ""})
    result = await tda.run_trade_devils_advocate_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["outcome"] == "ok"
    assert result["reviewed"] == 2

    # Deuxième passage : les deux positions sont déjà en base, rien à refaire.
    result2 = await tda.run_trade_devils_advocate_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result2 == {"outcome": "nothing_to_review", "checked": 2}


@pytest.mark.asyncio
async def test_cycle_respects_max_per_cycle_cap(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")
    monkeypatch.setattr(tda, "_MAX_PER_CYCLE", 2)

    async def positions_fetch():
        return [_position(id_=i) for i in range(1, 6)]

    llm = _llm_returning({"verdict": "sound", "flaw": "", "lesson": ""})
    result = await tda.run_trade_devils_advocate_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["reviewed"] == 2
    assert result["checked"] == 5


@pytest.mark.asyncio
async def test_cycle_one_failure_does_not_break_others(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")

    async def positions_fetch():
        return [_position(id_=1), _position(id_=2)]

    calls = {"n": 0}

    async def flaky_llm(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("panne réseau")
        return json.dumps({"verdict": "sound", "flaw": "", "lesson": ""})

    result = await tda.run_trade_devils_advocate_cycle(llm=flaky_llm, positions_fetch=positions_fetch)
    assert result["outcome"] == "ok"
    assert result["reviewed"] == 2
    outcomes = {r["position_id"]: r["verdict"] for r in result["results"]}
    assert outcomes[1] == "error"
    assert outcomes[2] == "sound"


@pytest.mark.asyncio
async def test_cycle_counts_flawed_verdicts(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")

    async def positions_fetch():
        return [_position(id_=1)]

    llm = _llm_returning({"verdict": "flawed", "flaw": "x", "lesson": "y"})
    result = await tda.run_trade_devils_advocate_cycle(llm=llm, positions_fetch=positions_fetch)
    assert result["flawed"] == 1


# ── active_lessons / format_lessons_line ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_lessons_respects_limit(monkeypatch):
    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "true")
    for i in range(5):
        llm = _llm_returning({"verdict": "flawed", "flaw": f"faille {i}", "lesson": f"leçon {i}"})
        await tda._review_one(_position(id_=i), llm=llm)
    lessons = await tda.active_lessons(limit=3)
    assert len(lessons) == 3


def test_format_lessons_line_empty():
    assert tda.format_lessons_line([]) == ""


def test_format_lessons_line_joins_multiple():
    lessons = [
        {"contract": "0xabc", "symbol": "MAGIC", "flaw": "x", "lesson": "vérifier le R/R après impact"},
        {"contract": "0xdef", "symbol": "BRIAN", "flaw": "y", "lesson": "surveiller les décoys narratifs"},
    ]
    line = tda.format_lessons_line(lessons)
    assert "MAGIC" in line
    assert "BRIAN" in line
    assert "vérifier le R/R après impact" in line


def test_format_lessons_line_truncates_long_content():
    lessons = [{"contract": "0xabc", "symbol": "X", "flaw": "y", "lesson": "z" * 500}]
    line = tda.format_lessons_line(lessons)
    assert len(line) < 500
    assert "…" in line


def test_format_lessons_line_skips_entries_without_lesson_text():
    lessons = [{"contract": "0xabc", "symbol": "X", "flaw": "y", "lesson": ""}]
    assert tda.format_lessons_line(lessons) == ""
