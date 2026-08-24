"""24/08 -- renamed /off,/on (paper) to /offpaper,/onpaper for naming
consistency with two new independent pauses added the same day: /offx,/onx
(X interactions) and /offshadow,/onshadow (the standalone shadow process).
Covers the Telegram handler layer only -- each pause module's own contract
(fail-open/fail-closed, state isolation) is covered in its own
test_*_pause.py file.
"""
from __future__ import annotations

import pytest

from aria_core import paper_pause, shadow_pause, x_pause
from aria_core.gateway import telegram_bot
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    configure_data_dir(tmp_path)
    yield


OWNER_ID = 7
OTHER_ID = 999


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
        self.callback_query = None


class FakeContext:
    def __init__(self):
        self.args: list[str] = []


def _set_owner(monkeypatch, owner_id: int = OWNER_ID) -> None:
    monkeypatch.setattr(telegram_bot.settings, "owner_chat_id", owner_id)


# --- /offpaper, /onpaper ------------------------------------------------


@pytest.mark.asyncio
async def test_offpaper_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OTHER_ID)
    await telegram_bot._handle_offpaper(update, FakeContext())
    assert paper_pause.is_paused() is False


@pytest.mark.asyncio
async def test_offpaper_then_onpaper_as_owner(monkeypatch):
    _set_owner(monkeypatch)
    await telegram_bot._handle_offpaper(FakeUpdate(OWNER_ID), FakeContext())
    assert paper_pause.is_paused() is True

    await telegram_bot._handle_onpaper(FakeUpdate(OWNER_ID), FakeContext())
    assert paper_pause.is_paused() is False


# --- /offx, /onx ---------------------------------------------------------


@pytest.mark.asyncio
async def test_offx_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OTHER_ID)
    await telegram_bot._handle_offx(update, FakeContext())
    assert x_pause.is_paused() is False


@pytest.mark.asyncio
async def test_offx_then_onx_as_owner(monkeypatch):
    _set_owner(monkeypatch)
    await telegram_bot._handle_offx(FakeUpdate(OWNER_ID), FakeContext())
    assert x_pause.is_paused() is True

    await telegram_bot._handle_onx(FakeUpdate(OWNER_ID), FakeContext())
    assert x_pause.is_paused() is False


@pytest.mark.asyncio
async def test_offx_never_touches_paper_or_shadow_pause(monkeypatch):
    _set_owner(monkeypatch)
    await telegram_bot._handle_offx(FakeUpdate(OWNER_ID), FakeContext())
    assert paper_pause.is_paused() is False
    assert shadow_pause.is_paused() is False


# --- /offshadow, /onshadow ------------------------------------------------


@pytest.mark.asyncio
async def test_offshadow_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OTHER_ID)
    await telegram_bot._handle_offshadow(update, FakeContext())
    assert shadow_pause.is_paused() is False


@pytest.mark.asyncio
async def test_offshadow_then_onshadow_as_owner(monkeypatch):
    _set_owner(monkeypatch)
    await telegram_bot._handle_offshadow(FakeUpdate(OWNER_ID), FakeContext())
    assert shadow_pause.is_paused() is True

    await telegram_bot._handle_onshadow(FakeUpdate(OWNER_ID), FakeContext())
    assert shadow_pause.is_paused() is False


@pytest.mark.asyncio
async def test_onshadow_when_not_paused_is_a_no_op(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_onshadow(update, FakeContext())
    assert "n'était pas en pause" in update.message.replies[0]


# --- /off, /on (grouped shortcut, distinct from /stop) ---------------------


@pytest.mark.asyncio
async def test_off_arms_all_four_categories(monkeypatch):
    from aria_core import outgoing_pause

    _set_owner(monkeypatch)
    await telegram_bot._handle_off(FakeUpdate(OWNER_ID), FakeContext())
    assert outgoing_pause.is_paused() is True
    assert paper_pause.is_paused() is True
    assert x_pause.is_paused() is True
    assert shadow_pause.is_paused() is True


@pytest.mark.asyncio
async def test_stop_alone_never_arms_the_other_three(monkeypatch):
    """/stop stays real-capital-only -- the whole point of separating it
    from /off."""
    _set_owner(monkeypatch)
    await telegram_bot._handle_stop(FakeUpdate(OWNER_ID), FakeContext())
    assert paper_pause.is_paused() is False
    assert x_pause.is_paused() is False
    assert shadow_pause.is_paused() is False


@pytest.mark.asyncio
async def test_on_lifts_all_four_categories(monkeypatch):
    from aria_core import outgoing_pause

    _set_owner(monkeypatch)
    await telegram_bot._handle_off(FakeUpdate(OWNER_ID), FakeContext())
    await telegram_bot._handle_on(FakeUpdate(OWNER_ID), FakeContext())
    assert outgoing_pause.is_paused() is False
    assert paper_pause.is_paused() is False
    assert x_pause.is_paused() is False
    assert shadow_pause.is_paused() is False


@pytest.mark.asyncio
async def test_onshadow_alone_lifts_shadow_even_while_off_stays_armed(monkeypatch):
    """The exact behavior the operator asked for: a single category can be
    resumed independently even while the grouped shortcut is still armed."""
    from aria_core import outgoing_pause

    _set_owner(monkeypatch)
    await telegram_bot._handle_off(FakeUpdate(OWNER_ID), FakeContext())
    await telegram_bot._handle_onshadow(FakeUpdate(OWNER_ID), FakeContext())
    assert shadow_pause.is_paused() is False
    assert outgoing_pause.is_paused() is True  # real capital stays blocked
    assert paper_pause.is_paused() is True
    assert x_pause.is_paused() is True


@pytest.mark.asyncio
async def test_off_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    await telegram_bot._handle_off(FakeUpdate(OTHER_ID), FakeContext())
    assert paper_pause.is_paused() is False
    assert x_pause.is_paused() is False
    assert shadow_pause.is_paused() is False


# --- /offreal, /onreal (pure aliases of /stop, /resume) ---------------------


@pytest.mark.asyncio
async def test_offreal_arms_only_real_capital(monkeypatch):
    from aria_core import outgoing_pause

    _set_owner(monkeypatch)
    # /offreal is registered as the exact same handler function as /stop --
    # calling _handle_stop directly is equivalent to what /offreal does.
    await telegram_bot._handle_stop(FakeUpdate(OWNER_ID), FakeContext())
    assert outgoing_pause.is_paused() is True
    assert paper_pause.is_paused() is False
    assert x_pause.is_paused() is False
    assert shadow_pause.is_paused() is False
