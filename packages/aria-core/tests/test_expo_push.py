import pytest

from aria_core.gateway import expo_push
from aria_core.push_tokens import register_push_token


def test_derive_title_body_strips_html_and_picks_event_line():
    text = (
        "🧪 SIMULATION — portefeuille papier 1 M$ (v8)\n"
        "<b>ACHAT FICTIF cbBTC</b>\n"
        "Contrat 0xabc\n"
        "Entrée 1.234 · taille 5,000 $ (0.5% du capital de départ)"
    )
    title, body = expo_push._derive_title_body(text, fallback_title="fallback")
    assert title == "ACHAT FICTIF cbBTC"
    assert "Contrat 0xabc" in body


def test_derive_title_body_empty_text_uses_fallback():
    title, body = expo_push._derive_title_body("", fallback_title="ARIA — Trading")
    assert title == "ARIA — Trading"
    assert body == ""


@pytest.mark.asyncio
async def test_notify_trading_ignores_limit_order_alerts():
    await register_push_token("ExponentPushToken[trade-1]")
    text = "<b>🎯 ORDRE LIMITE POSÉ (SWING, portefeuille papier, aucun argent réel)</b>\ncbBTC -- cible 1.5"
    sent = await expo_push.notify_trading(text)
    assert sent is False


@pytest.mark.asyncio
async def test_notify_trading_ignores_watching_alerts():
    await register_push_token("ExponentPushToken[trade-2]")
    text = "<b>👁️ ARIA se rapproche (SWING)</b>\ncbBTC -- surveillance active"
    sent = await expo_push.notify_trading(text)
    assert sent is False


@pytest.mark.asyncio
async def test_notify_trading_sends_on_buy_alert(monkeypatch):
    await register_push_token("ExponentPushToken[trade-3]")
    calls = []

    async def fake_send(title, body, *, channel_id):
        calls.append((title, body, channel_id))
        return True

    monkeypatch.setattr(expo_push, "send_expo_push", fake_send)
    text = "🧪 SIMULATION — portefeuille papier 1 M$ (v8)\n<b>ACHAT FICTIF cbBTC</b>\nContrat 0xabc"
    sent = await expo_push.notify_trading(text)
    assert sent is True
    assert calls[0][2] == expo_push.CHANNEL_TRADING


@pytest.mark.asyncio
async def test_notify_trading_sends_on_periodic_tracking(monkeypatch):
    await register_push_token("ExponentPushToken[trade-4]")
    calls = []

    async def fake_send(title, body, *, channel_id):
        calls.append(channel_id)
        return True

    monkeypatch.setattr(expo_push, "send_expo_push", fake_send)
    text = "🧪 SIMULATION — suivi positions ouvertes (portefeuille papier 1 M$, 2 positions ouvertes)\ncbBTC ..."
    sent = await expo_push.notify_trading(text)
    assert sent is True
    assert calls == [expo_push.CHANNEL_TRADING]


@pytest.mark.asyncio
async def test_notify_support_has_no_content_filter(monkeypatch):
    calls = []

    async def fake_send(title, body, *, channel_id):
        calls.append(channel_id)
        return True

    monkeypatch.setattr(expo_push, "send_expo_push", fake_send)
    sent = await expo_push.notify_support("Any generic admin message, e.g. a watchdog report.")
    assert sent is True
    assert calls == [expo_push.CHANNEL_SUPPORT]


@pytest.mark.asyncio
async def test_send_expo_push_returns_false_without_tokens():
    sent = await expo_push.send_expo_push("t", "b", channel_id=expo_push.CHANNEL_TRADING)
    assert sent is False
