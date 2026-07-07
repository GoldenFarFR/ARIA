"""Livraison du rapport VC par email — orchestration (kill-switch + rendu + envoi).

Aucun envoi réel : send_email et outgoing_pause sont mockés.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.skills import vc_delivery
from aria_core.skills.vc_analysis import VCResult

ADDR = "0x" + "a" * 40
_GEN = "06/07/2026 18:00 UTC"


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR, potentiel=7, risque="MODÉRÉ", these="Traction réelle.",
        recommandation="BUY", taille_pct=5.0, entree="marché",
        invalidation="perte $5k", cible="x2", rapport_detaille="## Analyse\nDétail.",
        donnees_insuffisantes=["équipe"], security_score=60, lite_verdict="CAUTION", llm_used=True,
    )
    base.update(kw)
    return VCResult(**base)


@pytest.mark.asyncio
async def test_delivery_blocked_when_paused(monkeypatch):
    """Kill-switch fail-closed : en pause, aucun email ne part."""
    monkeypatch.setattr(vc_delivery.outgoing_pause, "is_paused", lambda *, strict=False: True)
    send = AsyncMock()
    monkeypatch.setattr(vc_delivery, "send_email", send)

    ok, error = await vc_delivery.send_vc_report(_result(), generated_at=_GEN)

    assert ok is False
    assert "pause" in error.lower()
    send.assert_not_called()  # jamais d'envoi en pause


@pytest.mark.asyncio
async def test_delivery_uses_strict_killswitch(monkeypatch):
    """Vérifie que le kill-switch est interrogé en mode strict (fail-closed)."""
    seen = {}

    def _is_paused(*, strict=False):
        seen["strict"] = strict
        return False

    monkeypatch.setattr(vc_delivery.outgoing_pause, "is_paused", _is_paused)
    monkeypatch.setattr(vc_delivery, "_recipient", lambda env=None: "agentaria.zhc@gmail.com")
    monkeypatch.setattr(vc_delivery, "send_email", AsyncMock(return_value=(True, None)))

    await vc_delivery.send_vc_report(_result(), generated_at=_GEN)

    assert seen["strict"] is True


@pytest.mark.asyncio
async def test_delivery_no_recipient_configured(monkeypatch):
    monkeypatch.setattr(vc_delivery.outgoing_pause, "is_paused", lambda *, strict=False: False)
    monkeypatch.setattr(vc_delivery, "_recipient", lambda env=None: "")
    send = AsyncMock()
    monkeypatch.setattr(vc_delivery, "send_email", send)

    ok, error = await vc_delivery.send_vc_report(_result(), generated_at=_GEN)

    assert ok is False
    assert "destinataire" in error.lower()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_success_renders_and_sends(monkeypatch):
    monkeypatch.setattr(vc_delivery.outgoing_pause, "is_paused", lambda *, strict=False: False)
    monkeypatch.setattr(vc_delivery, "_recipient", lambda env=None: "agentaria.zhc@gmail.com")
    captured = {}

    async def _fake_send(*, to, subject, html_body, text_body=None, config=None):
        captured.update(to=to, subject=subject, html=html_body, text=text_body)
        return True, None

    monkeypatch.setattr(vc_delivery, "send_email", _fake_send)

    ok, error = await vc_delivery.send_vc_report(_result(), generated_at=_GEN)

    assert ok is True
    assert error is None
    assert captured["to"] == "agentaria.zhc@gmail.com"
    assert "ARIA Vanguard ZHC" in captured["html"]  # rapport HTML rendu
    assert "BUY" in captured["subject"]
    assert "validation humaine" in captured["text"].lower()  # disclaimer dans le fallback texte


@pytest.mark.asyncio
async def test_delivery_propagates_send_failure(monkeypatch):
    monkeypatch.setattr(vc_delivery.outgoing_pause, "is_paused", lambda *, strict=False: False)
    monkeypatch.setattr(vc_delivery, "_recipient", lambda env=None: "agentaria.zhc@gmail.com")
    monkeypatch.setattr(vc_delivery, "send_email", AsyncMock(return_value=(False, "SMTP non configuré")))

    ok, error = await vc_delivery.send_vc_report(_result(), generated_at=_GEN)

    assert ok is False
    assert "SMTP" in error


def test_recipient_prefers_explicit_then_smtp_user():
    assert vc_delivery._recipient({"ARIA_VC_REPORT_TO": "a@x.com", "ARIA_SMTP_USER": "b@x.com"}) == "a@x.com"
    assert vc_delivery._recipient({"ARIA_SMTP_USER": "b@x.com"}) == "b@x.com"
    assert vc_delivery._recipient({}) == ""


def test_no_financial_execution_imports():
    """Garde-fou dôme : la livraison email n'importe aucun chemin d'exécution financière."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(vc_delivery))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [f"{node.module}.{a.name}" for a in node.names]
    joined = " ".join(imported)
    for forbidden in ("wallet_guard", "resolve_spend", "acp_cli"):
        assert forbidden not in joined, f"import financier interdit : {forbidden}"
