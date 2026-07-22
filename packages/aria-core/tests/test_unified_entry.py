"""Crible unifié VC/Swing (22/07, tâche #1) -- garde-fous durs partagés (délégués à
momentum_entry.evaluate_hard_gates, déjà testés séparément), contexte riche mocké au
niveau de scan_base_token (patron déjà utilisé ailleurs dans le repo), LLM mocké.
"""
from __future__ import annotations

import json

import pytest

from aria_core import unified_entry as ue
from aria_core.services.dexscreener import PairSnapshot
from aria_core.skills.acp_onchain_scan import TokenScanContext
from aria_core.skills.entry_signals import EntrySignal

CONTRACT = "0x" + "d" * 40


def _pair(**overrides) -> PairSnapshot:
    base = {
        "pair_address": "0xpool", "price_usd": 1.5, "liquidity_usd": 150_000.0,
        "volume_24h_usd": 50_000.0, "base_symbol": "UNI", "base_address": CONTRACT.lower(),
        "pair_created_at": 1_700_000_000_000,
        "project_links": [{"label": "Site officiel", "url": "https://example.test"}],
    }
    base.update(overrides)
    return PairSnapshot(**base)


def _ctx(*, with_technical_signal: bool = True, **overrides) -> TokenScanContext:
    ctx = TokenScanContext(
        contract=CONTRACT, valid_address=True, pairs_found=1, best_pair=_pair(),
        security_score=80, lite_verdict="SAFE", data_source="dexscreener",
    )
    if with_technical_signal:
        ctx.ta_golden_pocket_signal = EntrySignal(
            present=True, entry=1.5, invalidation=1.0, target=2.5, rr=2.0,
        )
        ctx.ta_ema_fast, ctx.ta_ema_slow = 1.6, 1.4
        ctx.ta_macd_line, ctx.ta_macd_signal = 0.02, 0.01
        from aria_core.skills.indicators import Candle

        ctx.ta_candles = [Candle(ts=i, open=1.5, high=1.55, low=1.45, close=1.5) for i in range(20)]
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _patch_common(monkeypatch, *, hard_gate_hold=None, ctx=None, llm_response: dict | None = None):
    from aria_core import momentum_entry

    async def fake_hard_gates(contract, chain, *, current_regime=None):
        if hard_gate_hold is not None:
            return None, None, hard_gate_hold
        return _pair(), "honeypot clear (GoPlus)", None

    async def fake_scan(contract, **kwargs):
        return ctx if ctx is not None else _ctx()

    async def fake_theses(contract):
        return []

    async def fake_chat(*args, **kwargs):
        return json.dumps(llm_response) if llm_response is not None else None

    monkeypatch.setattr(momentum_entry, "evaluate_hard_gates", fake_hard_gates)
    monkeypatch.setattr(ue, "scan_base_token", fake_scan)
    monkeypatch.setattr(ue, "list_theses_for_token", fake_theses)
    monkeypatch.setattr(ue, "chat_with_context", fake_chat)


@pytest.mark.asyncio
async def test_hard_gate_rejection_propagated_unchanged(monkeypatch):
    hold = {"action": "HOLD", "chain": "base", "reasons": ["liquidité insuffisante"], "hold_reason": "insufficient_liquidity"}
    _patch_common(monkeypatch, hard_gate_hold=hold)
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result == hold


@pytest.mark.asyncio
async def test_llm_vc_only_opens_single_vc_signal(monkeypatch):
    _patch_common(monkeypatch, llm_response={
        "horizon": "vc", "potentiel": 9, "risque": "MODÉRÉ", "confiance_globale": "haute",
        "these_vc": "Produit réel, team doxxée, traction confirmée sur deux signaux.",
        "taille_pct_vc": 7.0, "cible_vc": "x30", "invalidation_vc": "perte de traction",
        "swing_valide": False, "resume_executif": "Conviction forte, horizon long.",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "BUY"
    assert len(result["signals"]) == 1
    sig = result["signals"][0]
    assert sig["strategy"] == "vc_thesis"
    assert sig["taille_pct"] == 7.0


@pytest.mark.asyncio
async def test_llm_swing_only_opens_single_momentum_signal(monkeypatch):
    _patch_common(monkeypatch, llm_response={
        "horizon": "swing", "potentiel": 4, "risque": "ÉLEVÉ", "confiance_globale": "moyenne",
        "swing_valide": True, "swing_these": "Setup golden pocket exploitable maintenant.",
        "resume_executif": "Pas de conviction fondamentale, setup technique franc.",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "BUY"
    assert len(result["signals"]) == 1
    sig = result["signals"][0]
    assert sig["strategy"] == "momentum"
    assert sig["rr"] == 2.0
    assert sig["align_score"] == 2


@pytest.mark.asyncio
async def test_llm_les_deux_opens_two_cumulable_signals(monkeypatch):
    _patch_common(monkeypatch, llm_response={
        "horizon": "les_deux", "potentiel": 8, "risque": "MODÉRÉ", "confiance_globale": "haute",
        "these_vc": "Conviction forte sur le produit.", "taille_pct_vc": 5.0,
        "cible_vc": "x20", "invalidation_vc": "perte de traction",
        "swing_valide": True, "swing_these": "Setup technique franc en plus.",
        "resume_executif": "Les deux axes convainquent.",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "BUY"
    strategies = {s["strategy"] for s in result["signals"]}
    assert strategies == {"vc_thesis", "momentum"}


@pytest.mark.asyncio
async def test_llm_aucun_holds(monkeypatch):
    _patch_common(monkeypatch, llm_response={
        "horizon": "aucun", "potentiel": 2, "risque": "EXTRÊME", "confiance_globale": "faible",
        "resume_executif": "Aucun axe ne convainc.",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "unified_no_conviction"


@pytest.mark.asyncio
async def test_swing_veto_when_no_technical_signal_even_if_llm_says_swing(monkeypatch):
    """Veto déterministe (comme le veto honeypot de vc_analysis) : le LLM ne peut
    jamais confirmer un setup swing sans R/R réel calculé -- ici aucune série OHLCV."""
    ctx_no_ta = _ctx(with_technical_signal=False)
    _patch_common(monkeypatch, ctx=ctx_no_ta, llm_response={
        "horizon": "swing", "swing_valide": True, "swing_these": "hallucination possible",
        "potentiel": 3, "risque": "ÉLEVÉ", "confiance_globale": "faible",
        "resume_executif": "test",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "unified_no_conviction"


@pytest.mark.asyncio
async def test_llm_unavailable_falls_back_to_no_position(monkeypatch):
    _patch_common(monkeypatch, llm_response=None)
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "unified_no_conviction"


@pytest.mark.asyncio
async def test_llm_unparsable_output_falls_back_to_no_position(monkeypatch):
    from aria_core import momentum_entry

    async def fake_hard_gates(contract, chain, *, current_regime=None):
        return _pair(), "honeypot clear (GoPlus)", None

    async def fake_scan(contract, **kwargs):
        return _ctx()

    async def fake_theses(contract):
        return []

    async def fake_chat(*args, **kwargs):
        return "ceci n'est pas du JSON valide"

    monkeypatch.setattr(momentum_entry, "evaluate_hard_gates", fake_hard_gates)
    monkeypatch.setattr(ue, "scan_base_token", fake_scan)
    monkeypatch.setattr(ue, "list_theses_for_token", fake_theses)
    monkeypatch.setattr(ue, "chat_with_context", fake_chat)

    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["action"] == "HOLD"
    assert result["hold_reason"] == "unified_no_conviction"


@pytest.mark.asyncio
async def test_taille_pct_vc_clamped_to_max(monkeypatch):
    _patch_common(monkeypatch, llm_response={
        "horizon": "vc", "taille_pct_vc": 999.0, "these_vc": "test", "cible_vc": "x50",
        "invalidation_vc": "test", "potentiel": 9, "risque": "FAIBLE", "confiance_globale": "haute",
        "resume_executif": "test",
    })
    result = await ue.evaluate_unified_entry(CONTRACT, "base")
    assert result["signals"][0]["taille_pct"] == ue.MAX_POSITION_SIZE_PCT
