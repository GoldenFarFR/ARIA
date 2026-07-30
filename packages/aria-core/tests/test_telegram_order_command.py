"""/order -- lists every currently active (pending/watching) limit order,
grouped by pocket (30/07, real gap found: no Telegram command exposed
limit_orders.get_active_orders() at all). Aucun réseau : get_active_orders
est mocké."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
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
async def test_order_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    get_orders_mock = AsyncMock()
    monkeypatch.setattr("aria_core.limit_orders.get_active_orders", get_orders_mock)

    update = FakeUpdate("/order")
    await telegram_bot._handle_order(update, FakeContext())

    assert len(update.message.replies) == 1
    get_orders_mock.assert_not_called()


@pytest.mark.asyncio
async def test_order_reports_empty_list(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.limit_orders.get_active_orders", AsyncMock(return_value=[]))

    update = FakeUpdate("/order")
    await telegram_bot._handle_order(update, FakeContext())

    assert "aucun ordre" in update.message.replies[-1].lower()


@pytest.mark.asyncio
async def test_order_groups_by_wallet_and_shows_details(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    orders = [
        {
            "wallet": "scalping", "symbol": "AERO", "chain": "base", "state": "pending",
            "target_price": 0.4322, "expires_at": "2026-07-30T20:02:26.828504+00:00",
            "contract": "0x" + "a" * 40,
        },
        {
            "wallet": "scalping", "symbol": "TIG", "chain": "base", "state": "watching",
            "target_price": 1.016, "expires_at": "2026-07-30T18:00:46.149580+00:00",
            "contract": "0x" + "b" * 40,
        },
        {
            "wallet": "swing", "symbol": "BRETT", "chain": "base", "state": "watching",
            "target_price": 0.003903, "expires_at": "2026-08-19T14:19:40.041093+00:00",
            "contract": "0x" + "c" * 40,
        },
    ]
    monkeypatch.setattr("aria_core.limit_orders.get_active_orders", AsyncMock(return_value=orders))

    update = FakeUpdate("/order")
    await telegram_bot._handle_order(update, FakeContext())

    report = update.message.replies[-1]
    assert "3 au total" in report
    assert "[scalping] (2)" in report
    assert "[swing] (1)" in report
    assert "AERO" in report and "TIG" in report and "BRETT" in report
    assert "0.4322" in report
    assert "🆕 pending" in report
    assert "⏳ watching" in report
    # 30/07, operator request: a real DexScreener link per token, reusing the
    # existing services/dexscreener.py::token_url (no new URL pattern).
    assert f"https://dexscreener.com/base/0x{'a' * 40}" in report
    assert f"https://dexscreener.com/base/0x{'b' * 40}" in report
    assert f"https://dexscreener.com/base/0x{'c' * 40}" in report


@pytest.mark.asyncio
async def test_order_caps_display_per_wallet_without_hiding_the_count(monkeypatch):
    """No silent cap: beyond the display ceiling, the overflow is stated
    explicitly rather than a truncated list that reads as complete."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    orders = [
        {
            "wallet": "scalping", "symbol": f"TOK{i}", "chain": "base", "state": "watching",
            "target_price": 1.0, "expires_at": "2026-07-30T20:00:00+00:00",
        }
        for i in range(30)
    ]
    monkeypatch.setattr("aria_core.limit_orders.get_active_orders", AsyncMock(return_value=orders))

    update = FakeUpdate("/order")
    await telegram_bot._handle_order(update, FakeContext())

    report = update.message.replies[-1]
    assert "30 au total" in report
    assert "de plus, non affichés" in report
