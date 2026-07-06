"""Étape B — commande Telegram /vc + formatage de l'ordre court.

Aucun appel réseau : analyze_vc est mocké. Vérifie la restriction admin, la
validation d'adresse, et le formatage de l'ordre (proposition, jamais exécution).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.vc_analysis import VCResult, format_telegram_order

ADDR = "0x" + "a" * 40


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR,
        potentiel=7,
        risque="MODÉRÉ",
        these="Traction on-chain réelle.",
        recommandation="BUY",
        taille_pct=5.0,
        entree="marché",
        invalidation="perte support $5k",
        cible="x2 6 mois",
        llm_used=True,
    )
    base.update(kw)
    return VCResult(**base)


# ----------------------- format_telegram_order -----------------------


def test_format_buy_order_contains_actionable_fields():
    out = format_telegram_order(_result())
    assert "Ordre proposé" in out
    assert "BUY" in out
    assert "5.0% du capital" in out
    assert "Invalidation" in out
    assert "Tangem" in out  # disclaimer validation manuelle
    assert "automatique" in out.lower()


def test_format_watch_has_no_order_and_no_size():
    out = format_telegram_order(_result(recommandation="WATCH", taille_pct=0.0))
    assert "pas d'ordre" in out.lower()
    assert "du capital" not in out


def test_format_fallback_flags_llm_disabled():
    out = format_telegram_order(
        _result(recommandation="WATCH", taille_pct=0.0, potentiel=None, llm_used=False)
    )
    assert "n/a" in out
    assert "llm désactivé" in out.lower()


def test_format_always_has_manual_execution_disclaimer():
    for reco in ("BUY", "SELL", "WATCH", "AVOID"):
        out = format_telegram_order(_result(recommandation=reco))
        assert "manuelle" in out.lower()


# ----------------------- /vc handler -----------------------


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


@pytest.mark.asyncio
async def test_vc_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()


@pytest.mark.asyncio
async def test_vc_rejects_invalid_address(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)

    update = FakeUpdate("/vc pas-une-adresse")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()
    assert "invalide" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_vc_valid_runs_analysis_and_sends_order(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock(return_value=_result())
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)
    # Auto-log prédiction mocké (pas de DB dans ce test).
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=42))
    # Email mocké (succès) — on vérifie juste le câblage, pas l'envoi réel.
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_awaited_once_with(ADDR)
    send_report.assert_awaited_once()
    # replies : "en cours", ordre, log prédiction, statut email
    assert len(update.message.replies) == 4
    assert "BUY" in update.message.replies[1]
    assert "Tangem" in update.message.replies[1]
    assert "#42" in update.message.replies[2]
    assert "email" in update.message.replies[3].lower()


@pytest.mark.asyncio
async def test_vc_reports_email_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", AsyncMock(return_value=_result()))
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=7))
    # Email en échec (SMTP non configuré) — le handler ne doit pas crasher.
    monkeypatch.setattr(
        "aria_core.skills.vc_delivery.send_vc_report",
        AsyncMock(return_value=(False, "SMTP non configuré")),
    )

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    assert len(update.message.replies) == 4
    assert "non envoyé" in update.message.replies[3].lower()
