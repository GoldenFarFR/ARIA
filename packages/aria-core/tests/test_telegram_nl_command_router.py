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
async def test_ledger_nl_trigger_matches_operator_exact_phrasing(monkeypatch):
    """19/07 -- reproduit l'incident réel : "tu a des positions ouverte ?"
    (formulation directe de l'opérateur sur Telegram, sans "détail") tombait
    dans la conversation LLM générale et confabulait (mauvais capital,
    "aucune position" alors qu'une position était réellement ouverte)."""

    async def fake_build_report(*, closed_limit=500):
        return ("Ledger FAKE", {"machine": "dict"})

    monkeypatch.setattr("aria_core.paper_ledger_report.build_report", fake_build_report)
    reply = await telegram_bot._try_nl_readonly_command("tu a des positions ouverte ?")
    assert reply == "Ledger FAKE"


@pytest.mark.asyncio
async def test_ledger_nl_trigger_not_confused_with_opinion_sense_of_position():
    """"position" seul (sans "ouverte") reste ambigu en français (avis/opinion
    vs. position de trading) -- ne doit PAS déclencher le ledger."""
    reply = await telegram_bot._try_nl_readonly_command(
        "tu as une position sur ce sujet politique ?"
    )
    assert reply is None


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


# ── alias mots-clés nus (20/07, trou réel : "Watchlist" tapé seul = 11857 tokens) ────

@pytest.mark.asyncio
async def test_bare_watchlist_word_matches_exactly_the_operator_incident(monkeypatch):
    """Reproduit l'incident réel (capture Telegram) : le mot "Watchlist" tapé SEUL
    (capitalisé, aucune phrase autour) ne matchait aucun des 7 déclencheurs de
    phrase -- tombait dans la conversation LLM générale, payante (11857 tokens)."""
    async def fake_report(n=10, *, lister=None):
        return "WATCHLIST FAKE"

    monkeypatch.setattr("aria_core.skills.candidate_ranking.format_watchlist_report", fake_report)
    reply = await telegram_bot._try_nl_readonly_command("Watchlist")
    assert reply == "WATCHLIST FAKE"


@pytest.mark.asyncio
async def test_bare_watchlist_tolerates_case_and_trailing_punctuation(monkeypatch):
    async def fake_report(n=10, *, lister=None):
        return "WATCHLIST FAKE"

    monkeypatch.setattr("aria_core.skills.candidate_ranking.format_watchlist_report", fake_report)
    for variant in ("watchlist", "WATCHLIST", "  watchlist  ", "watchlist ?", "watchlist!"):
        reply = await telegram_bot._try_nl_readonly_command(variant)
        assert reply == "WATCHLIST FAKE", f"échec sur la variante {variant!r}"


@pytest.mark.asyncio
async def test_bare_portfolio_word_routes_to_feedback(monkeypatch):
    """"Portfolio" n'avait aucun déclencheur du tout avant ce correctif (ni phrase,
    ni alias) -- mappé sur le bilan agrégé (départ/PnL/résultat), la lecture la
    plus proche de ce mot."""
    async def fake_feedback_reply():
        return "FEEDBACK FAKE"

    monkeypatch.setattr(telegram_bot, "_feedback_reply", fake_feedback_reply)
    for variant in ("Portfolio", "portfolio", "feedback", "Bilan"):
        reply = await telegram_bot._try_nl_readonly_command(variant)
        assert reply == "FEEDBACK FAKE", f"échec sur la variante {variant!r}"


@pytest.mark.asyncio
async def test_feedback_sentence_trigger_returns_real_data(monkeypatch):
    async def fake_feedback_reply():
        return "FEEDBACK FAKE"

    monkeypatch.setattr(telegram_bot, "_feedback_reply", fake_feedback_reply)
    reply = await telegram_bot._try_nl_readonly_command("c'est quoi le résultat du portefeuille ?")
    assert reply == "FEEDBACK FAKE"


@pytest.mark.asyncio
async def test_bare_alias_covers_the_other_five_commands(monkeypatch):
    """Non-régression généralisée : chaque commande NL déjà sûre gagne aussi son
    alias mot-nu, pas seulement watchlist/feedback."""
    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.compute_readiness_scorecard",
        lambda: _async_return({"fake": "scorecard"}),
    )
    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.format_readiness_report", lambda s: "FEUVERT FAKE"
    )
    monkeypatch.setattr(
        "aria_core.skills.market_sentiment.latest_readings", lambda: _async_return([{"fake": 1}])
    )
    monkeypatch.setattr("aria_core.skills.market_sentiment.format_sentiment_report", lambda r: "SENTIMENT FAKE")
    monkeypatch.setattr("aria_core.vc_predictions.format_track_report", lambda: _async_return("TRACK FAKE"))
    monkeypatch.setattr(
        "aria_core.agent_wallet_monitor.get_wallet_balance_summary", lambda: _async_return({"fake": 1})
    )
    monkeypatch.setattr(
        "aria_core.agent_wallet_monitor.format_wallet_balance_summary", lambda s: "AGENTWALLET FAKE"
    )
    monkeypatch.setattr(
        "aria_core.paper_ledger_report.build_report",
        lambda *, closed_limit=500: _async_return(("LEDGER FAKE", {})),
    )

    assert await telegram_bot._try_nl_readonly_command("feu vert") == "FEUVERT FAKE"
    assert await telegram_bot._try_nl_readonly_command("Sentiment") == "SENTIMENT FAKE"
    assert await telegram_bot._try_nl_readonly_command("track") == "TRACK FAKE"
    assert await telegram_bot._try_nl_readonly_command("wallet agent") == "AGENTWALLET FAKE"
    assert await telegram_bot._try_nl_readonly_command("ledger") == "LEDGER FAKE"


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner()


@pytest.mark.asyncio
async def test_word_containing_watchlist_substring_in_a_longer_sentence_does_not_bare_match():
    """L'alias nu exige une correspondance EXACTE du texte normalisé entier --
    "watchlist" simplement présent au milieu d'une phrase ne doit jamais matcher
    par ce chemin (protège contre un faux positif trop large)."""
    reply = await telegram_bot._try_nl_readonly_command("je regarde watchlist plus tard peut-être")
    assert reply is None


@pytest.mark.asyncio
async def test_handle_message_short_circuits_on_bare_watchlist_word(monkeypatch):
    """Intégration bout en bout de l'incident réel : "Watchlist" tapé seul ne doit
    jamais atteindre le LLM payant."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_report(n=10, *, lister=None):
        return "WATCHLIST FAKE"

    monkeypatch.setattr("aria_core.skills.candidate_ranking.format_watchlist_report", fake_report)

    async def fake_process(*args, **kwargs):
        raise AssertionError("le LLM ne doit jamais être appelé sur ce chemin")

    monkeypatch.setattr(type(telegram_bot.aria_brain), "process", fake_process)

    update = FakeUpdate("Watchlist")
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == ["WATCHLIST FAKE"]


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
