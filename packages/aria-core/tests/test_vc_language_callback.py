"""Callback Telegram ``vclang:<lang>:<address>`` — choix de langue avant l'envoi
du rapport /vc (chemin réel, hors mode test).

Aucun réseau : analyse et envoi mockés. Vérifie : gate admin, adresse invalide
ignorée en sécurité, retrait des boutons après clic, langue transmise à l'analyse.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.vc_analysis import VCResult

ADDR = "0x" + "a" * 40


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR, potentiel=7, risque="MODÉRÉ", these="Traction réelle.",
        recommandation="BUY", taille_pct=5.0, entree="marché", invalidation="perte $5k",
        cible="x2", llm_used=True,
    )
    base.update(kw)
    return VCResult(**base)


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []
        self.reply_markups: list[object] = []
        self.edit_calls = 0

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append(text)
        self.reply_markups.append(reply_markup)

    async def edit_reply_markup(self, reply_markup=None) -> None:
        self.edit_calls += 1


class FakeUser:
    def __init__(self, user_id: int = 42):
        self.id = user_id


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage, user_id: int = 42):
        self.data = data
        self.message = message
        self.from_user = FakeUser(user_id)
        self.answered = False

    async def answer(self, *args, **kwargs) -> None:
        self.answered = True


class FakeCallbackUpdate:
    def __init__(self, query: FakeQuery, user_id: int = 42):
        self.callback_query = query
        self.effective_user = FakeUser(user_id)
        self.message = None


class FakeContext:
    def __init__(self):
        self.args = []


def _mock_pipeline(monkeypatch):
    # Le chemin normal appelle désormais analyze_vc_with_context (15/07, #158-adjacent
    # fix) pour pouvoir renseigner entry_price/pool_address -- ctx.best_pair=None ici,
    # ces tests ne portent pas sur le suivi wallet.
    analyze = AsyncMock(return_value=(_result(), SimpleNamespace(best_pair=None)))
    monkeypatch.setattr("aria_core.skills.vc_analysis.analyze_vc_with_context", analyze)
    monkeypatch.setattr("aria_core.vc_predictions.record_prediction", AsyncMock(return_value=1))
    monkeypatch.setattr("aria_core.vc_predictions.count_predictions_for_contract", AsyncMock(return_value=0))
    monkeypatch.setattr("aria_core.vc_predictions.total_predictions_count", AsyncMock(return_value=0))
    send_report = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("aria_core.skills.vc_delivery.send_vc_report", send_report)
    return analyze, send_report


@pytest.mark.asyncio
async def test_lang_button_triggers_analysis_in_chosen_lang(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze, send_report = _mock_pipeline(monkeypatch)

    message = FakeMessage()
    query = FakeQuery(f"vclang:en:{ADDR}", message)
    update = FakeCallbackUpdate(query)

    await telegram_bot._handle_callback(update, FakeContext())

    assert query.answered is True
    assert message.edit_calls == 1  # boutons retirés (un seul choix possible)
    analyze.assert_awaited_once_with(ADDR, lang="en")
    _, kwargs = send_report.call_args
    assert kwargs["lang"] == "en"


@pytest.mark.asyncio
async def test_lang_button_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze, _send = _mock_pipeline(monkeypatch)

    message = FakeMessage()
    query = FakeQuery(f"vclang:fr:{ADDR}", message)
    update = FakeCallbackUpdate(query)

    await telegram_bot._handle_callback(update, FakeContext())

    analyze.assert_not_called()


@pytest.mark.asyncio
async def test_lang_button_ignores_malformed_address(monkeypatch):
    """Défense en profondeur : un callback_data trafiqué avec une adresse invalide
    n'est jamais transmis à l'analyse (même si l'admin-check passe)."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze, send_report = _mock_pipeline(monkeypatch)

    message = FakeMessage()
    query = FakeQuery("vclang:fr:not-an-address", message)
    update = FakeCallbackUpdate(query)

    await telegram_bot._handle_callback(update, FakeContext())

    analyze.assert_not_called()
    send_report.assert_not_called()


@pytest.mark.asyncio
async def test_lang_button_normalizes_invalid_lang_to_fr(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    analyze, _send = _mock_pipeline(monkeypatch)

    message = FakeMessage()
    query = FakeQuery(f"vclang:zz:{ADDR}", message)
    update = FakeCallbackUpdate(query)

    await telegram_bot._handle_callback(update, FakeContext())

    analyze.assert_awaited_once_with(ADDR, lang="fr")
