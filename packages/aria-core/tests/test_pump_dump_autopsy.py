"""Autopsie pump/dump (tâche #8) — hors-ligne, tout injecté. Vérifie : la détection
déterministe (aucun LLM), le gating, le dédoublonnage, et les deux canaux de sortie
(log local + proposition GitHub jamais un commit/fusion)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from aria_core import vc_predictions
from aria_core.skills import pump_dump_autopsy as pda
from aria_core.skills.ta_levels import Candle


class _FakeGitHubClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/77"}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pda, "DB_PATH", str(tmp_path / "autopsy_test.db"))
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "vc_pred_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    yield


def _candle(ts, *, high, close):
    return Candle(ts=ts, open=close, high=high, low=close, close=close, volume=100.0)


def _now_ts() -> int:
    """Epoch réel courant -- `record_prediction` pose `created_at` sur le vrai 'now',
    donc les bougies factices de bout-en-bout doivent être postérieures à un vrai
    epoch, pas de petits entiers arbitraires (sinon `since_ts` les filtre toutes)."""
    return int(datetime.now(timezone.utc).timestamp())


# ── detect_pump_dump (pure, déterministe) ──────────────────────────────────────────

def test_detect_pump_dump_real_pattern():
    candles = [_candle(100, high=1.0, close=1.0), _candle(200, high=4.0, close=3.8), _candle(300, high=4.0, close=1.5)]
    result = pda.detect_pump_dump(candles, entry_price=1.0, since_ts=100)
    assert result is not None
    assert result["peak_multiple"] == 4.0
    assert result["drawdown_pct"] == pytest.approx(0.625)


def test_detect_pump_dump_no_pump_returns_none():
    candles = [_candle(100, high=1.05, close=1.0), _candle(200, high=1.1, close=1.05)]
    assert pda.detect_pump_dump(candles, entry_price=1.0, since_ts=100) is None


def test_detect_pump_dump_pump_without_dump_returns_none():
    """Pic réel, mais le prix est resté haut (pas de vraie retombée) -- pas un pump/dump."""
    candles = [_candle(100, high=1.0, close=1.0), _candle(200, high=3.0, close=2.9)]
    assert pda.detect_pump_dump(candles, entry_price=1.0, since_ts=100) is None


def test_detect_pump_dump_ignores_pre_entry_spike():
    """Un pic AVANT l'entrée (since_ts) ne doit jamais compter -- sinon un token
    déjà retombé au moment de l'analyse serait faussement accusé de pump/dump."""
    candles = [_candle(50, high=10.0, close=9.0), _candle(200, high=1.1, close=1.05)]
    assert pda.detect_pump_dump(candles, entry_price=1.0, since_ts=100) is None


def test_detect_pump_dump_empty_or_missing_entry():
    assert pda.detect_pump_dump([], entry_price=1.0) is None
    assert pda.detect_pump_dump([_candle(100, high=2.0, close=1.0)], entry_price=None) is None
    assert pda.detect_pump_dump([_candle(100, high=2.0, close=1.0)], entry_price=0) is None


# ── gating ──────────────────────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", raising=False)
    assert pda.pump_dump_autopsy_enabled() is False


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", raising=False)
    result = await pda.run_pump_dump_autopsy_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await pda.run_pump_dump_autopsy_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_cycle_nothing_to_autopsy(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    result = await pda.run_pump_dump_autopsy_cycle()
    assert result["outcome"] == "nothing_to_autopsy"


# ── run_pump_dump_autopsy_cycle : bout-en-bout ─────────────────────────────────────

async def _record_and_close(*, contract, entry_price, pool, outcome_pct=10.0):
    pred_id = await vc_predictions.record_prediction(
        contract=contract, recommandation="BUY", potentiel=8, risque="MODÉRÉ",
        taille_pct=5.0, security_score=70, llm_used=True, report_ref="test",
        entry_price=entry_price, pool_address=pool, network="base",
        target_price=entry_price * 2, invalidation_price=entry_price * 0.8,
    )
    await vc_predictions.close_prediction(pred_id, outcome_pct=outcome_pct, note="test")
    return pred_id


@pytest.mark.asyncio
async def test_full_cycle_autopsies_and_proposes_playbook(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    pred_id = await _record_and_close(contract="0x" + "a" * 40, entry_price=1.0, pool="0xpool1")

    async def fake_ohlcv(pool, network):
        return [_candle(_now_ts(), high=1.0, close=1.0), _candle(_now_ts() + 60, high=4.0, close=1.1)]

    async def fake_llm(prompt, system, *, max_tokens=500, model=None, depth=None,
                        provider=None, fallback_provider=None, fallback_model=None):
        assert "4.0x" in prompt or "4.0" in prompt
        return json.dumps({
            "lesson": "Le risque MODÉRÉ annoncé n'anticipait pas un pic à 4x suivi d'un crash.",
            "durable": True,
            "proposal_title": "Pattern : pic 4x+ puis retour proche de l'entrée",
            "proposal_body": "Motif observé...",
        })

    fake_github = _FakeGitHubClient()
    result = await pda.run_pump_dump_autopsy_cycle(
        ohlcv_fetch=fake_ohlcv, llm=fake_llm, github_client=fake_github,
    )

    assert result["outcome"] == "ok"
    assert result["autopsied"] == 1
    assert len(fake_github.calls) == 1
    assert fake_github.calls[0]["labels"] == ["aria-playbook-proposal"]
    assert result["results"][0]["prediction_id"] == pred_id


@pytest.mark.asyncio
async def test_non_durable_lesson_does_not_open_github_issue(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    await _record_and_close(contract="0x" + "b" * 40, entry_price=1.0, pool="0xpool2")

    async def fake_ohlcv(pool, network):
        return [_candle(_now_ts(), high=1.0, close=1.0), _candle(_now_ts() + 60, high=3.0, close=1.0)]

    async def fake_llm(prompt, system, *, max_tokens=500, model=None, depth=None,
                        provider=None, fallback_provider=None, fallback_model=None):
        return json.dumps({"lesson": "Cas isolé, rien de généralisable.", "durable": False, "proposal_title": "", "proposal_body": ""})

    fake_github = _FakeGitHubClient()
    result = await pda.run_pump_dump_autopsy_cycle(
        ohlcv_fetch=fake_ohlcv, llm=fake_llm, github_client=fake_github,
    )

    assert result["autopsied"] == 1
    assert fake_github.calls == []


@pytest.mark.asyncio
async def test_no_pattern_does_not_call_llm(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    await _record_and_close(contract="0x" + "c" * 40, entry_price=1.0, pool="0xpool3")

    async def fake_ohlcv(pool, network):
        return [_candle(_now_ts(), high=1.02, close=1.0), _candle(_now_ts() + 60, high=1.05, close=1.02)]

    llm_calls = []

    async def fake_llm(prompt, system, *, max_tokens=500, model=None, depth=None,
                        provider=None, fallback_provider=None, fallback_model=None):
        llm_calls.append(prompt)
        return json.dumps({"lesson": "should not be called", "durable": False, "proposal_title": "", "proposal_body": ""})

    result = await pda.run_pump_dump_autopsy_cycle(ohlcv_fetch=fake_ohlcv, llm=fake_llm)

    assert result["autopsied"] == 0
    assert llm_calls == []


@pytest.mark.asyncio
async def test_never_autopsies_same_prediction_twice(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    await _record_and_close(contract="0x" + "d" * 40, entry_price=1.0, pool="0xpool4")

    call_count = {"n": 0}

    async def fake_ohlcv(pool, network):
        call_count["n"] += 1
        return [_candle(_now_ts(), high=1.0, close=1.0), _candle(_now_ts() + 60, high=3.0, close=1.0)]

    async def fake_llm(prompt, system, *, max_tokens=500, model=None, depth=None,
                        provider=None, fallback_provider=None, fallback_model=None):
        return json.dumps({"lesson": "leçon", "durable": False, "proposal_title": "", "proposal_body": ""})

    await pda.run_pump_dump_autopsy_cycle(ohlcv_fetch=fake_ohlcv, llm=fake_llm)
    second = await pda.run_pump_dump_autopsy_cycle(ohlcv_fetch=fake_ohlcv, llm=fake_llm)

    assert call_count["n"] == 1  # pas re-fetché la deuxième fois
    assert second["outcome"] == "nothing_to_autopsy"


@pytest.mark.asyncio
async def test_one_failing_case_does_not_break_the_others(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    await _record_and_close(contract="0x" + "e" * 40, entry_price=1.0, pool="0xbroken")
    await _record_and_close(contract="0x" + "f" * 40, entry_price=1.0, pool="0xok")

    async def fake_ohlcv(pool, network):
        if pool == "0xbroken":
            raise RuntimeError("OHLCV indisponible")
        return [_candle(_now_ts(), high=1.0, close=1.0), _candle(_now_ts() + 60, high=3.0, close=1.0)]

    async def fake_llm(prompt, system, *, max_tokens=500, model=None, depth=None,
                        provider=None, fallback_provider=None, fallback_model=None):
        return json.dumps({"lesson": "leçon", "durable": False, "proposal_title": "", "proposal_body": ""})

    result = await pda.run_pump_dump_autopsy_cycle(ohlcv_fetch=fake_ohlcv, llm=fake_llm)

    outcomes = {r["outcome"] for r in result["results"]}
    assert "error" in outcomes
    assert "autopsied" in outcomes


@pytest.mark.asyncio
async def test_missing_pool_or_entry_price_is_skipped_not_crashed(monkeypatch):
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    pred_id = await vc_predictions.record_prediction(
        contract="0x" + "9" * 40, recommandation="BUY", potentiel=5, risque="MODÉRÉ",
        taille_pct=3.0, security_score=60, llm_used=True, report_ref="test",
    )
    await vc_predictions.close_prediction(pred_id, outcome_pct=0.0, note="test")

    result = await pda.run_pump_dump_autopsy_cycle()

    assert result["results"][0]["outcome"] == "skipped_no_pool_or_entry"


# ── routage explicite Sonnet 5 + secours Haiku (17/07) ──────────────────────

@pytest.mark.asyncio
async def test_cycle_routes_to_sonnet5_via_openrouter_with_haiku_fallback(monkeypatch):
    """Même bascule que claude_mentor.py -- voir son test jumeau pour le détail de
    la revue de raisonnement profond ayant motivé ce choix."""
    monkeypatch.setenv("ARIA_PUMP_DUMP_AUTOPSY_ENABLED", "1")
    await _record_and_close(contract="0x" + "b" * 40, entry_price=1.0, pool="0xpool2")

    async def fake_ohlcv(pool, network):
        return [_candle(_now_ts(), high=1.0, close=1.0), _candle(_now_ts() + 60, high=4.0, close=1.1)]

    captured = {}

    async def capturing_llm(prompt, system, **kwargs):
        captured.update(kwargs)
        return json.dumps({"lesson": "leçon", "durable": False, "proposal_title": "", "proposal_body": ""})

    await pda.run_pump_dump_autopsy_cycle(ohlcv_fetch=fake_ohlcv, llm=capturing_llm)
    assert captured.get("provider") == "openrouter"
    assert captured.get("model") == "anthropic/claude-sonnet-5"
    assert captured.get("fallback_provider") == "openrouter"
    assert captured.get("fallback_model") == "anthropic/claude-haiku-4.5"
    assert captured.get("max_tokens") == 900
