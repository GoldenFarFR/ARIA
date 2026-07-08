import pytest


@pytest.mark.asyncio
async def test_relay_recent_requires_access_token(tmp_path, monkeypatch):
    from aria_core import relay_chat
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay.db"))
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_header = await client.get("/api/aria/relay/recent")
        wrong_header = await client.get(
            "/api/aria/relay/recent", headers={"X-Relay-Access": "wrong"}
        )

    assert no_header.status_code == 403
    assert wrong_header.status_code == 403


@pytest.mark.asyncio
async def test_relay_recent_and_reply_roundtrip(tmp_path, monkeypatch):
    from aria_core import relay_chat
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay.db"))
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")

    async def fake_sender(text):
        return True

    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.send_message", fake_sender, raising=False,
    )

    await relay_chat.log_message("operator", "Une vraie question.")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        recent = await client.get(
            "/api/aria/relay/recent", headers={"X-Relay-Access": "secret123"}
        )
        reply = await client.post(
            "/api/aria/relay/reply",
            json={"text": "Voici ma réponse."},
            headers={"X-Relay-Access": "secret123"},
        )

    assert recent.status_code == 200
    assert recent.json()["messages"][0]["content"] == "Une vraie question."

    assert reply.status_code == 200
    assert reply.json() == {"ok": True}
