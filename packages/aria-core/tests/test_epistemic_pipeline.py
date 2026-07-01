"""Tests pipeline épistémique Phase B."""

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.knowledge.calibration_ledger import (
    compute_stats,
    record_calibration,
    record_prediction,
)
from aria_core.knowledge.contradiction import check_contradiction
from aria_core.knowledge.epistemic_critic import critic_check
from aria_core.knowledge.web_verify import WebSource, fetch_web_snippets, should_web_verify


def test_query_variants_world_cup():
    from aria_core.knowledge.web_verify import _query_variants

    variants = _query_variants("matchs coupe du monde 2026 aujourd'hui")
    assert len(variants) >= 2
    assert any("FIFA" in v or "2026" in v for v in variants)


def test_should_web_verify_uncertain():
    assert should_web_verify({"p_true": 0.4, "truth": "INCERTAIN"}) is True
    assert should_web_verify({"p_true": 0.9, "truth": "VRAI"}) is False


@pytest.mark.asyncio
async def test_fetch_web_snippets_empty():
    out = await fetch_web_snippets("xy")
    assert isinstance(out, list)


def test_calibration_ledger_brier(tmp_path, monkeypatch):
    import aria_core.knowledge.calibration_ledger as cl

    monkeypatch.setattr(cl, "LEDGER_PATH", tmp_path / "cal.json")
    pred_id = record_prediction("test?", "reply", p_true=0.8, truth="vrai")
    cal = record_calibration("test claim", "faux", prediction_id=pred_id)
    assert cal.get("brier") is not None
    stats = compute_stats()
    assert stats["resolved"] >= 1


def test_contradiction_holding():
    conflict, _ = check_contradiction("DEXPulse est la holding mère", "fr")
    assert conflict is True


@pytest.mark.asyncio
async def test_critic_flags_metrics():
    safe, adjusted, meta = await critic_check(
        "Nos revenus sont de 50000$ ce mois sans preuve",
        "fr",
    )
    assert meta.get("critic") == "flagged"
    assert "Non vérifié" in adjusted or "Unverified" in adjusted


@pytest.mark.asyncio
@patch("aria_core.llm.is_llm_configured", return_value=True)
@patch("aria_core.llm.chat_with_context", new_callable=AsyncMock)
@patch("aria_core.knowledge.web_verify.fetch_web_snippets", new_callable=AsyncMock)
async def test_resolve_with_web_verify(mock_snip, mock_chat, _cfg, tmp_path, monkeypatch):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path,
        settings=AriaRuntimeSettings(aria_epistemic_web_verify=True),
    )
    mock_snip.return_value = [WebSource(text="Emmanuel Macron est président (wiki)")]
    mock_chat.return_value = (
        "FAIT: VRAI\nREPONSE: Emmanuel Macron.\nP_VRAI: 0.92\nP_FAUX: 0.08\nRAISON: web"
    )
    from aria_core.knowledge.epistemic import resolve_calibrated_answer

    with patch(
        "aria_core.knowledge.epistemic.groq_calibrated_answer",
        new_callable=AsyncMock,
    ) as mock_groq:
        mock_groq.return_value = (
            "Réponse calibrée (Groq)\n\nIncertain.\n\nFAIT : incertain\nP(vrai)=0.40",
            {"groq_calibrated": True, "p_true": 0.4, "truth": "INCERTAIN"},
        )
        reply, data = await resolve_calibrated_answer("president france", "fr")
        assert data.get("web_verified") or data.get("web_verify")
        assert reply