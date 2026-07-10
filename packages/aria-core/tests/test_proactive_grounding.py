"""founder_ping ancré sur l'état réel (pool + track-record) + responsabilisation sur sa
dernière initiative — avant ce correctif, c'était du texte LLM pur, sans lien avec les
vraies données (candidat inventé possible, aucun suivi de promesse)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_core import proactive
from aria_core.runtime import get_settings
from aria_core.skills.candidate_ranking import RankedCandidate


def _fake_candidate(symbol="GOOD", score=82.0, verdict="SAFE"):
    return RankedCandidate(
        contract="0x" + "a" * 40, symbol=symbol, rank_score=score,
        security_score=78, liquidity_usd=50_000.0, top_holder_pct=12.0, verdict=verdict,
    )


@pytest.mark.asyncio
async def test_real_state_snapshot_includes_pool_and_track_record(monkeypatch):
    async def fake_top_candidates(n):
        return [_fake_candidate()]

    async def fake_total_count():
        return 7

    async def fake_open_preds(limit=1):
        return [{"id": 1}]

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )

    snapshot = await proactive._real_state_snapshot()
    assert "GOOD" in snapshot
    assert "SAFE" in snapshot
    assert "7 pronostic" in snapshot
    assert "au moins un pronostic ouvert" in snapshot


@pytest.mark.asyncio
async def test_real_state_snapshot_empty_pool_says_so(monkeypatch):
    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )

    snapshot = await proactive._real_state_snapshot()
    assert "aucun candidat disponible" in snapshot
    assert "0 pronostic" in snapshot
    assert "aucun pronostic ouvert" in snapshot


@pytest.mark.asyncio
async def test_real_state_snapshot_degrades_gracefully_on_failure(monkeypatch):
    async def boom(n):
        raise RuntimeError("db down")

    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", boom)

    # Ne doit jamais lever -- best-effort, dégradation douce.
    snapshot = await proactive._real_state_snapshot()
    assert isinstance(snapshot, str)


def test_last_initiative_recap_returns_last_entry(monkeypatch):
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory",
        lambda category, limit: ["HH:MM:SS UTC]\nAncienne idée", "HH:MM:SS UTC]\nDernière idée"],
    )
    recap = proactive._last_initiative_recap()
    assert "Dernière idée" in recap


def test_last_initiative_recap_empty_when_no_history(monkeypatch):
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )
    assert proactive._last_initiative_recap() == ""


@pytest.mark.asyncio
async def test_run_founder_ping_injects_real_state_and_accountability(monkeypatch):
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return [_fake_candidate(symbol="REALTOKEN")]

    async def fake_total_count():
        return 3

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory",
        lambda category, limit: ["HH:MM:SS UTC]\nJ'ai promis un pronostic hier"],
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        reply = await proactive.run_founder_ping(lang="fr")

    assert reply == "Verdict : ok."
    system = captured["system"]
    assert "REALTOKEN" in system
    assert "3 pronostic" in system
    assert "J'ai promis un pronostic hier" in system
    assert "DERNIÈRE initiative" in system


@pytest.mark.asyncio
async def test_run_founder_ping_no_last_initiative_no_accountability_block(monkeypatch):
    settings = get_settings()
    settings.aria_proactive_ideas = True
    settings.aria_llm_enabled = True
    settings.llm_api_key = "x"
    settings.llm_provider = "groq"
    settings.telegram_bot_token = "t"
    settings.telegram_admin_ids = "1"

    async def fake_top_candidates(n):
        return []

    async def fake_total_count():
        return 0

    async def fake_open_preds(limit=1):
        return []

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.top_candidates", fake_top_candidates
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.total_predictions_count", fake_total_count
    )
    monkeypatch.setattr(
        "aria_core.vc_predictions.list_open_predictions", fake_open_preds
    )
    monkeypatch.setattr(
        "aria_core.proactive.read_recent_memory", lambda category, limit: []
    )

    captured = {}

    async def fake_chat(user, system, **kwargs):
        captured["system"] = system
        return "Verdict : ok."

    with patch("aria_core.proactive.build_llm_context", new=AsyncMock(return_value="contexte")):
        monkeypatch.setattr("aria_core.proactive.chat_with_context", fake_chat)
        await proactive.run_founder_ping(lang="fr")

    assert "DERNIÈRE initiative" not in captured["system"]
