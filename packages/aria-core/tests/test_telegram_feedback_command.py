"""/feedback -- bilan paper-trading admin-only (#197, 15/07). Format demandé : départ /
PnL total / résultat, données déjà calculées par paper_trader.portfolio_summary(),
jamais câblées à une commande Telegram avant ce chantier.

19/07 : /feedback inclut désormais aussi le détail par position (thèse, URL
DexScreener) via paper_ledger_report.build_positions_detail_block() -- les tests
d'agrégat ci-dessous le mockent (ils testent le HEADER, pas le détail) ; un test
dédié plus bas vérifie le vrai détail avec une position réelle."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aria_core.gateway import telegram_bot


async def _fake_empty_detail_block(**kwargs):
    return "--- Positions ouvertes (0) ---\n  (aucune)\n\n--- Positions clôturées récentes (0) ---\n  (aucune)"


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


def test_feedback_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "feedback" in all_commands


@pytest.mark.asyncio
async def test_feedback_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/feedback", user_id=123)
    await telegram_bot._handle_feedback(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "restricted" in update.message.replies[0].lower() or "administrator" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_feedback_shows_starting_pnl_and_result(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_summary(*, price_lookup=None, wallet="swing"):
        return {
            "starting": 1_000_000.0,
            "cash": 950_000.0,
            "equity": 1_050_000.0,
            "return_pct": 5.0,
            "realized_pnl": 30_000.0,
            "unrealized_pnl": 20_000.0,
            "open_positions": 2,
            "closed_trades": 3,
            "win_rate": 66.7,
        }

    monkeypatch.setattr("aria_core.paper_trader.portfolio_summary", fake_summary)
    monkeypatch.setattr(
        "aria_core.paper_ledger_report.build_positions_detail_block", _fake_empty_detail_block,
    )

    update = FakeUpdate("/feedback", user_id=42)
    await telegram_bot._handle_feedback(update, FakeContext())

    reply = update.message.replies[0]
    assert "1,000,000" in reply  # départ
    assert "+50,000" in reply  # PnL total = 30k + 20k
    assert "1,050,000" in reply  # résultat = départ + PnL total


@pytest.mark.asyncio
async def test_feedback_result_equals_starting_plus_pnl_total(monkeypatch):
    """Vérifie explicitement l'identité départ + PnL total == résultat (equity) demandée
    par l'opérateur -- pas juste affichée, réellement vraie pour ce jeu de données."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_summary(*, price_lookup=None, wallet="swing"):
        return {
            "starting": 500_000.0, "cash": 480_000.0, "equity": 465_000.0,
            "return_pct": -7.0, "realized_pnl": -10_000.0, "unrealized_pnl": -25_000.0,
            "open_positions": 1, "closed_trades": 1, "win_rate": 0.0,
        }

    monkeypatch.setattr("aria_core.paper_trader.portfolio_summary", fake_summary)
    monkeypatch.setattr(
        "aria_core.paper_ledger_report.build_positions_detail_block", _fake_empty_detail_block,
    )

    update = FakeUpdate("/feedback", user_id=42)
    await telegram_bot._handle_feedback(update, FakeContext())

    reply = update.message.replies[0]
    assert "500,000" in reply
    assert "-35,000" in reply  # -10k + -25k
    assert "465,000" in reply  # 500k - 35k


@pytest.mark.asyncio
async def test_feedback_includes_open_position_detail_with_url(monkeypatch, tmp_path):
    """19/07 -- demande opérateur explicite : /feedback doit montrer le détail de
    chaque position en cours, pas seulement le bilan agrégé. Utilise une vraie
    position (pas un mock du détail) pour vérifier bout en bout que
    build_positions_detail_block() est bien câblée.

    24/07 -- demande opérateur explicite (visuelle) : le rendu par position est
    désormais le format compact (une ligne, lien DexScreener collé à la même
    ligne) au lieu du bloc multi-lignes avec thèse/R:R -- voir la docstring de
    build_positions_detail_block. La thèse complète reste consultable via
    /ledger, jamais supprimée du système, seulement plus affichée ici."""
    from aria_core import paper_trader as pt

    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    import asyncio as _asyncio

    monkeypatch.setattr(pt, "_run_cycle_lock", _asyncio.Lock())
    await pt.reset_portfolio(1_000_000.0)
    await pt.open_position(
        "0x" + "a" * 40, "COBOT", 0.0001439,
        target_price=0.000165774, invalidation_price=0.000137165,
        alloc_usd=50_000, thesis="honeypot clear; golden pocket + divergence RSI; R/R 2.5", wallet="swing",
    )

    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    update = FakeUpdate("/feedback", user_id=42)
    await telegram_bot._handle_feedback(update, FakeContext())

    reply = update.message.replies[0]
    assert "COBOT" in reply
    assert "dexscreener.com" in reply.lower()
    assert "Positions ouvertes (1)" in reply
    # The link is glued to the SAME line as the position stats.
    for line in reply.splitlines():
        if line.startswith("COBOT"):
            assert "dexscreener.com" in line.lower()
            break
    else:
        pytest.fail("no line starting with COBOT found")


@pytest.mark.asyncio
async def test_feedback_shows_all_3_pockets_distinctly(monkeypatch):
    """27/07 -- 3-pocket architecture plan, Phase 5: /feedback must show
    scalping/swing/vc side by side, each with ITS OWN numbers -- not a single
    aggregate that silently hides two of the three pockets."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    per_wallet = {
        "scalping": {
            "starting": 1_000_000.0, "cash": 990_000.0, "equity": 995_000.0,
            "return_pct": -0.5, "realized_pnl": -3_000.0, "unrealized_pnl": -2_000.0,
            "open_positions": 1, "closed_trades": 4, "win_rate": 50.0,
        },
        "swing": {
            "starting": 1_000_000.0, "cash": 900_000.0, "equity": 1_050_000.0,
            "return_pct": 5.0, "realized_pnl": 30_000.0, "unrealized_pnl": 20_000.0,
            "open_positions": 3, "closed_trades": 2, "win_rate": 100.0,
        },
        "vc": {
            "starting": 1_000_000.0, "cash": 1_000_000.0, "equity": 1_000_000.0,
            "return_pct": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
            "open_positions": 0, "closed_trades": 0, "win_rate": None,
        },
    }

    async def fake_summary(*, price_lookup=None, wallet="swing"):
        return per_wallet[wallet]

    monkeypatch.setattr("aria_core.paper_trader.portfolio_summary", fake_summary)
    monkeypatch.setattr(
        "aria_core.paper_ledger_report.build_positions_detail_block", _fake_empty_detail_block,
    )

    update = FakeUpdate("/feedback", user_id=42)
    await telegram_bot._handle_feedback(update, FakeContext())

    reply = update.message.replies[0]
    assert "Scalping" in reply
    assert "Swing" in reply
    assert "VC" in reply
    # Each pocket's own numbers appear, distinctly -- never a single merged figure.
    assert "995,000" in reply  # scalping equity
    assert "1,050,000" in reply  # swing equity
    assert "1,000,000" in reply  # vc equity (starting == equity, flat)


@pytest.mark.asyncio
async def test_feedback_no_message_or_user_does_not_crash():
    class EmptyUpdate:
        message = None
        effective_user = None

    await telegram_bot._handle_feedback(EmptyUpdate(), FakeContext())  # ne doit pas lever
