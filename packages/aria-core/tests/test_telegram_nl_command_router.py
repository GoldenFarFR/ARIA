"""Routage langage naturel -> commandes en lecture seule (18/07, #213). Demande
opérateur explicite : "si je demande a aria pour lui demander sa watchlist elle
lance elle meme /watchlist [...] la liste des / elle me la donne comme toi au
dessus". Scope validé ("1. ok") : uniquement des commandes SANS effet de bord et
SANS paramètre requis -- jamais /these, /issue, /canal, /x, /stop, /vc, /scan...

Zéro appel réseau réel : chaque source de données est mockée."""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)

    async def reply_chat_action(self, _action: str) -> None:
        pass


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


# ── _try_nl_readonly_command -- détection + réponse réelle par commande ─────────────

@pytest.mark.asyncio
async def test_commands_list_returns_real_alphabetized_list():
    reply = await telegram_bot._try_nl_readonly_command("liste tes commandes stp")
    assert reply is not None
    assert "/watchlist" in reply
    assert "/status" in reply
    # Reflète TELEGRAM_MENU_COMMANDS tel quel -- jamais une 2e liste séparée.
    names_in_reply = [line.split(" ")[0].lstrip("/") for line in reply.splitlines() if line.startswith("/")]
    expected = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert names_in_reply == expected


@pytest.mark.asyncio
async def test_watchlist_nl_trigger_returns_real_data(monkeypatch):
    async def fake_report(n=10, *, lister=None):
        return "👀 Contrats suivis de près (2/10 demandés) :\n\nFAKE"

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.format_watchlist_report", fake_report
    )
    reply = await telegram_bot._try_nl_readonly_command("montre-moi ta watchlist")
    assert reply == "👀 Contrats suivis de près (2/10 demandés) :\n\nFAKE"


@pytest.mark.asyncio
async def test_feuvert_nl_trigger_returns_real_data(monkeypatch):
    async def fake_scorecard():
        return {"fake": "scorecard"}

    def fake_format(scorecard):
        assert scorecard == {"fake": "scorecard"}
        return "Scorecard FAKE"

    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.compute_readiness_scorecard", fake_scorecard
    )
    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.format_readiness_report", fake_format
    )
    reply = await telegram_bot._try_nl_readonly_command("le feu vert est à combien ?")
    assert reply == "Scorecard FAKE"


@pytest.mark.asyncio
async def test_sentiment_nl_trigger_returns_real_data(monkeypatch):
    async def fake_readings():
        return [{"fake": "reading"}]

    def fake_format(readings):
        assert readings == [{"fake": "reading"}]
        return "Sentiment FAKE"

    monkeypatch.setattr("aria_core.skills.market_sentiment.latest_readings", fake_readings)
    monkeypatch.setattr("aria_core.skills.market_sentiment.format_sentiment_report", fake_format)
    reply = await telegram_bot._try_nl_readonly_command("c'est quoi le sentiment du marché ?")
    assert reply == "Sentiment FAKE"


@pytest.mark.asyncio
async def test_track_nl_trigger_returns_real_data(monkeypatch):
    async def fake_report():
        return "Track record FAKE"

    monkeypatch.setattr("aria_core.vc_predictions.format_track_report", fake_report)
    reply = await telegram_bot._try_nl_readonly_command("c'est quoi ton track record ?")
    assert reply == "Track record FAKE"


@pytest.mark.asyncio
async def test_agentwallet_nl_trigger_returns_real_data(monkeypatch):
    async def fake_summary(*, wallet_address="", chain="base"):
        return {"fake": "summary"}

    def fake_format(summary):
        assert summary == {"fake": "summary"}
        return "Solde FAKE"

    monkeypatch.setattr("aria_core.agent_wallet_monitor.get_wallet_balance_summary", fake_summary)
    monkeypatch.setattr("aria_core.agent_wallet_monitor.format_wallet_balance_summary", fake_format)
    reply = await telegram_bot._try_nl_readonly_command("le solde du wallet agent stp")
    assert reply == "Solde FAKE"


@pytest.mark.asyncio
async def test_ledger_nl_trigger_returns_real_data(monkeypatch):
    async def fake_build_report(*, closed_limit=500):
        assert closed_limit == 10
        return ("Ledger FAKE", {"machine": "dict"})

    monkeypatch.setattr("aria_core.paper_ledger_report.build_report", fake_build_report)
    reply = await telegram_bot._try_nl_readonly_command("je veux le détail des positions")
    assert reply == "Ledger FAKE"


@pytest.mark.asyncio
async def test_no_match_returns_none():
    reply = await telegram_bot._try_nl_readonly_command("salut, comment ça va aujourd'hui ?")
    assert reply is None


@pytest.mark.asyncio
async def test_ordinary_price_question_not_confused_with_watchlist():
    """Un mot isolé ("surveiller") au sens générique ne doit pas suffire --
    seule une formulation proche de l'intention réelle matche."""
    reply = await telegram_bot._try_nl_readonly_command(
        "il faut surveiller ce marché de près, qu'en penses-tu ?"
    )
    assert reply is None


# ── Intégration : _handle_message court-circuite bien AVANT le pipeline LLM ──────────

@pytest.mark.asyncio
async def test_handle_message_short_circuits_on_watchlist_question(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_report(n=10, *, lister=None):
        return "WATCHLIST FAKE"

    monkeypatch.setattr(
        "aria_core.skills.candidate_ranking.format_watchlist_report", fake_report
    )

    llm_called = {"value": False}

    async def fake_process(*args, **kwargs):
        llm_called["value"] = True
        raise AssertionError("le LLM ne doit jamais être appelé sur ce chemin")

    # Patcher la CLASSE, jamais l'instance -- monkeypatch.setattr(instance, ...)
    # capture le bound method réel comme "ancienne valeur" et le réécrit tel
    # quel sur l'instance à la revert, créant une pollution permanente
    # (l'instance masque alors la classe pour TOUJOURS, cassant tout
    # monkeypatch(type(aria_brain), "process", ...) fait par un test ultérieur
    # dans le même process -- bug réel trouvé le 18/07 en creusant 4 tests qui
    # échouaient de façon insaisissable seulement en suite complète).
    monkeypatch.setattr(type(telegram_bot.aria_brain), "process", fake_process)

    update = FakeUpdate("dis, ta watchlist ressemble à quoi en ce moment ?")
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == ["WATCHLIST FAKE"]
    assert llm_called["value"] is False


@pytest.mark.asyncio
async def test_handle_message_non_admin_never_reaches_nl_router(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)

    called = {"nl_router": False}

    async def fake_nl_router(_text):
        called["nl_router"] = True
        return "ne devrait jamais être atteint"

    async def fake_public(_update, _text):
        return None

    monkeypatch.setattr(telegram_bot, "_try_nl_readonly_command", fake_nl_router)
    monkeypatch.setattr(telegram_bot, "_handle_public_message", fake_public)

    update = FakeUpdate("ta watchlist ?", user_id=999)
    await telegram_bot._handle_message(update, context=None)

    assert called["nl_router"] is False
