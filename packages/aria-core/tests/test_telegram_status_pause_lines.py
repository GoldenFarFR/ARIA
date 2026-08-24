"""24/08 -- /status was never updated when the /off split (paper/x/shadow,
independent of /stop) shipped the same day: an operator checking "what's
armed" saw only 2 of 5 flags (sorties/custody), the exact blind spot that
made the shadow-pocket bug (curve tracker gated on the wrong flag) invisible
from this command. Covers only the 3 new lines added -- the rest of
_handle_status (LLM/GitHub/X config, heartbeat) is pre-existing, untested
dead weight, not a regression introduced here.
"""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    configure_data_dir(tmp_path)
    yield


OWNER_ID = 7


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int):
        self.message = FakeMessage()
        self.effective_user = FakeUser(user_id)


class FakeContext:
    def __init__(self):
        self.args: list[str] = []


def _stub_status_deps(monkeypatch):
    """Mocks every dependency /status pulls in that isn't the pause flags
    under test -- none of it is what this test verifies."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: True)
    monkeypatch.setattr(
        telegram_bot.aria_heartbeat, "get_status", lambda: {"last_heartbeat": None}
    )
    import aria_core.llm as llm_mod
    monkeypatch.setattr(llm_mod, "is_llm_configured", lambda: False)
    monkeypatch.setattr(llm_mod, "is_llm_provider_configured", lambda: False)
    import aria_core.skills.github_skill as gh_mod
    monkeypatch.setattr(gh_mod, "github_configured", lambda: False)
    monkeypatch.setattr(gh_mod, "github_unlimited_access", lambda: False)
    import aria_core.gateway.x_twitter as x_mod
    monkeypatch.setattr(x_mod, "is_x_post_configured", lambda: False)
    monkeypatch.setattr(x_mod, "is_x_read_configured", lambda: False)
    monkeypatch.setattr(x_mod, "is_x_reading_active", lambda: False)


@pytest.mark.asyncio
async def test_status_shows_all_five_pause_flags_when_nothing_armed(monkeypatch):
    _stub_status_deps(monkeypatch)
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_status(update, FakeContext())

    text = update.message.replies[0]
    assert "Sorties (capital réel, /stop): actives" in text
    assert "Custody (dépenses réelles wallet agent): actives" in text
    assert "Paper trading 1M$ (/offpaper): actives" in text
    assert "X posts/replies (/offx): actives" in text
    assert "Poches shadow (/offshadow): actives" in text


@pytest.mark.asyncio
async def test_status_shows_shadow_pause_armed(monkeypatch):
    _stub_status_deps(monkeypatch)
    from aria_core import shadow_pause

    shadow_pause.pause(by=OWNER_ID)
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_status(update, FakeContext())

    text = update.message.replies[0]
    assert "Poches shadow (/offshadow): ⏸ EN PAUSE" in text
    # the other 4 flags are untouched by arming shadow alone
    assert "Sorties (capital réel, /stop): actives" in text
    assert "X posts/replies (/offx): actives" in text
    assert "Paper trading 1M$ (/offpaper): actives" in text
