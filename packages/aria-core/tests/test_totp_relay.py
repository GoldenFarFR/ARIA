import pytest

from aria_core.totp_relay import (
    RELAY_ENABLED,
    create_request,
    poll_request,
    try_fulfill_from_admin_message,
)


@pytest.fixture(autouse=True)
def isolated_relay(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.totp_relay.data_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_relay_disabled_by_default():
    assert RELAY_ENABLED is False
    created = await create_request(machine="PC-operateur", purpose="vault-sync")
    assert created["disabled"] is True
    assert created["reason"] == "totp_ide_only"

    polled = poll_request("abc")
    assert polled["status"] == "disabled"

    reply = await try_fulfill_from_admin_message("123456", 1)
    assert reply is None


@pytest.mark.asyncio
async def test_create_poll_fulfill_when_enabled(monkeypatch):
    monkeypatch.setattr("aria_core.totp_relay.RELAY_ENABLED", True)

    async def fake_notify(_text: str) -> bool:
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.notify_admin", fake_notify)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.is_admin", lambda _uid: True)

    created = await create_request(machine="PC-operateur", purpose="vault-sync")
    rid = created["request_id"]
    assert created["telegram_notified"] is True

    pending = poll_request(rid)
    assert pending["status"] == "pending"

    reply = await try_fulfill_from_admin_message("482910", 12345)
    assert reply is not None
    assert "Code recu" in reply

    done = poll_request(rid)
    assert done["status"] == "fulfilled"
    assert done["code"] == "482910"