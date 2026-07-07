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


def test_format_buy_order_with_capital_shows_dollar_amount():
    out = format_telegram_order(_result(taille_pct=5.0), capital_usd=1500)
    assert "5.0% du capital" in out
    assert "$75" in out  # 5% de 1500
    assert "$1,500" in out


def test_format_buy_order_without_capital_no_dollar_amount():
    out = format_telegram_order(_result())
    assert "≈ $" not in out


def test_format_buy_order_ignores_invalid_capital():
    out = format_telegram_order(_result(), capital_usd=0)
    assert "≈ $" not in out


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
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=1))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=46))
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
    # Numérotation transmise à l'envoi (n° par token = compteur+1, série globale = total+1).
    _, kwargs = send_report.call_args
    assert kwargs["report_number"] == 2
    assert kwargs["series_number"] == 47


@pytest.mark.asyncio
async def test_vc_uses_capital_env_var_for_dollar_amount(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", AsyncMock(return_value=_result(taille_pct=5.0)))
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=1))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", AsyncMock(return_value=(True, None)))
    monkeypatch.setenv("ARIA_CAPITAL_USD", "1500")

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    assert "$75" in update.message.replies[1]  # 5% de $1500


# ----------------------- /vc MODE TEST admin -----------------------


def _test_mode_mocks(monkeypatch, reasoning: str = "Analyse détaillée : Techno solide, équipe doxxée, traction on-chain réelle."):
    """Câble tous les mocks + renvoie les mocks sensibles (email + track-record)."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze = AsyncMock(return_value=_result(rapport_detaille=reasoning))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)
    record = AsyncMock(return_value=42)
    count = AsyncMock(return_value=1)
    total = AsyncMock(return_value=46)
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", record)
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", count)
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", total)
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)
    return analyze, send_report, record, count, total


@pytest.mark.asyncio
async def test_vc_test_mode_shows_reasoning_no_email_no_record(monkeypatch):
    analyze, send_report, record, count, total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    # L'analyse tourne normalement...
    analyze.assert_awaited_once_with(ADDR)
    # ...mais AUCUN email et AUCUNE écriture/incrément track-record.
    send_report.assert_not_called()
    record.assert_not_called()
    count.assert_not_called()
    total.assert_not_called()

    joined = "\n".join(update.message.replies)
    assert "MODE TEST" in joined
    assert "non envoyé" in joined.lower()
    assert "non enregistré" in joined.lower()
    # Raisonnement complet affiché.
    assert "Techno solide" in joined
    # Ordre formaté toujours affiché.
    assert "BUY" in joined


@pytest.mark.asyncio
async def test_vc_test_mode_flag_is_case_insensitive(monkeypatch):
    _analyze, send_report, record, _count, _total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR} TEST")
    await telegram_bot._handle_vc(update, FakeContext())

    send_report.assert_not_called()
    record.assert_not_called()
    assert "MODE TEST" in "\n".join(update.message.replies)


@pytest.mark.asyncio
async def test_vc_test_mode_truncates_long_reasoning(monkeypatch):
    long_reasoning = "X" * 5000
    _analyze, _send, _record, _count, _total = _test_mode_mocks(monkeypatch, reasoning=long_reasoning)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    joined = "\n".join(update.message.replies)
    assert "tronqué" in joined
    # Chaque message reste sous la limite Telegram gérée par _reply (4000).
    assert all(len(r) <= 4000 for r in update.message.replies)


@pytest.mark.asyncio
async def test_vc_address_alone_is_normal_mode(monkeypatch):
    """Une adresse seule (sans `test`) = mode normal : email + enregistrement appelés."""
    _analyze, send_report, record, _count, _total = _test_mode_mocks(monkeypatch)

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    send_report.assert_awaited_once()
    record.assert_awaited_once()
    joined = "\n".join(update.message.replies)
    assert "MODE TEST" not in joined


@pytest.mark.asyncio
async def test_vc_test_mode_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze = AsyncMock()
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", analyze)
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)

    update = FakeUpdate(f"/vc {ADDR} test")
    await telegram_bot._handle_vc(update, FakeContext())

    analyze.assert_not_called()
    send_report.assert_not_called()


@pytest.mark.asyncio
async def test_vc_reports_email_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc", AsyncMock(return_value=_result()))
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=7))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=6))
    # Email en échec (SMTP non configuré) — le handler ne doit pas crasher.
    monkeypatch.setattr(
        "aria_core.skills.vc_delivery.send_vc_report",
        AsyncMock(return_value=(False, "SMTP non configuré")),
    )

    update = FakeUpdate(f"/vc {ADDR}")
    await telegram_bot._handle_vc(update, FakeContext())

    assert len(update.message.replies) == 4
    assert "non envoyé" in update.message.replies[3].lower()
