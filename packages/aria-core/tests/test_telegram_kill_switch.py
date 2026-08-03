"""Item #198 (29/07) -- automatic, immediate kill-switch on an unexpected_
outflow (operator explicit request: "systeme blinde... si ca arrive le
compte bloque tout swap ou tout transfert", owner-only lift: "seul ladresse
de l'owner peut permettre un transfert de fond").

Covers the manual side of the kill-switch (/stop, /resume, and the
"outflow_confirm" Telegram callback that lifts an auto-armed pause) --
the automatic-arm side is covered in test_agent_wallet_monitor.py. Also
covers the append-only incident log (kill_incident_log) added alongside
this item: every arm/lift, manual or automatic, must leave its own trace
rather than overwriting a single pause_state.json snapshot."""
from __future__ import annotations

import pytest

from aria_core import custody_pause, kill_incident_log, outgoing_pause
from aria_core.gateway import telegram_bot
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)  # isolates pause_state.json
    monkeypatch.setattr(kill_incident_log, "DB_PATH", str(tmp_path / "kill_incident_test.db"))
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


class FakeQuery:
    def __init__(self, user_id: int):
        self.from_user = FakeUser(user_id)
        self.answered_alerts: list[str] = []
        self.edited_texts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        if show_alert:
            self.answered_alerts.append(text)

    async def edit_message_text(self, text) -> None:
        self.edited_texts.append(text)


def _set_owner(monkeypatch, owner_id: int = OWNER_ID) -> None:
    monkeypatch.setattr(telegram_bot.settings, "owner_chat_id", owner_id)


# --- /stop -------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OTHER_ID)
    await telegram_bot._handle_stop(update, FakeContext())

    assert outgoing_pause.is_paused() is False
    assert "propriétaire" in update.message.replies[0].lower()
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_stop_as_owner_arms_pause_and_logs_incident(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_stop(update, FakeContext())

    assert outgoing_pause.is_paused() is True
    history = await kill_incident_log.list_incidents()
    assert len(history) == 1
    assert history[0]["event_type"] == "armed"
    assert history[0]["trigger_source"] == "manual"
    assert history[0]["by"] == str(OWNER_ID)


# --- /resume -------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_rejects_non_owner(monkeypatch):
    _set_owner(monkeypatch)
    outgoing_pause.pause(by="test")
    update = FakeUpdate(OTHER_ID)
    await telegram_bot._handle_resume(update, FakeContext())

    assert outgoing_pause.is_paused() is True  # still paused
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_resume_when_not_paused_is_a_noop_and_logs_nothing(monkeypatch):
    _set_owner(monkeypatch)
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_resume(update, FakeContext())

    assert "rien à reprendre" in update.message.replies[0].lower()
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_resume_as_owner_lifts_pause_and_logs_incident(monkeypatch):
    _set_owner(monkeypatch)
    outgoing_pause.pause(by="test", reason="incident test")
    update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_resume(update, FakeContext())

    assert outgoing_pause.is_paused() is False
    history = await kill_incident_log.list_incidents()
    assert len(history) == 1
    assert history[0]["event_type"] == "lifted"
    assert history[0]["trigger_source"] == "manual"
    assert history[0]["by"] == str(OWNER_ID)


# --- outflow_confirm callback --------------------------------------------


def _fake_result(tx_hash: str):
    from types import SimpleNamespace

    return SimpleNamespace(action=f"outflow_confirm:{tx_hash}")


@pytest.mark.asyncio
async def test_outflow_confirm_reject_keeps_pause_and_logs_nothing(monkeypatch):
    """"PAS autorisé" (approved=False) must never touch the pause state --
    it was already armed automatically, this branch just confirms it stays.
    Item #62 (08/03): the auto-arm/outflow_confirm pair now uses the
    dedicated custody_pause flag, not the shared outgoing_pause."""
    _set_owner(monkeypatch)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: True)
    tx_hash = "0xabc"
    custody_pause.pause(by="auto:agent_wallet_monitor", reason=f"... tx {tx_hash}")

    async def _fake_resolve_approval(approval_id, approved, by):
        assert approved is False
        return _fake_result(tx_hash)

    monkeypatch.setattr(telegram_bot, "resolve_approval", _fake_resolve_approval)

    query = FakeQuery(OWNER_ID)
    query.data = "reject:42"
    update = FakeUpdate(OWNER_ID)
    update.callback_query = query

    await telegram_bot._handle_callback(update, FakeContext())

    assert custody_pause.is_paused() is True
    assert "toujours actif" in query.edited_texts[0].lower() or "kill-switch" in query.edited_texts[0].lower()
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_outflow_confirm_approve_rejects_non_owner(monkeypatch):
    """"Autorisé par moi" must be owner-gated even though `_admin_check_reply`
    already let a plain admin through earlier in `_handle_callback` -- this is
    the STRICTER, owner-only check specific to lifting the kill-switch."""
    _set_owner(monkeypatch)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: True)  # non-owner admin
    tx_hash = "0xabc"
    custody_pause.pause(by="auto:agent_wallet_monitor", reason=f"... tx {tx_hash}")

    async def _fake_resolve_approval(approval_id, approved, by):
        assert approved is True
        return _fake_result(tx_hash)

    monkeypatch.setattr(telegram_bot, "resolve_approval", _fake_resolve_approval)

    query = FakeQuery(OTHER_ID)
    query.data = f"approve:42"
    update = FakeUpdate(OTHER_ID)
    update.callback_query = query

    await telegram_bot._handle_callback(update, FakeContext())

    assert custody_pause.is_paused() is True  # never lifted by a non-owner
    assert query.answered_alerts  # a "seul le owner..." alert was shown
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_outflow_confirm_approve_as_owner_with_matching_tx_lifts_pause(monkeypatch):
    _set_owner(monkeypatch)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: True)
    tx_hash = "0xabc123"
    custody_pause.pause(
        by="auto:agent_wallet_monitor",
        reason=f"Sortie non initiee par ARIA detectee automatiquement (wallet w, tx {tx_hash})",
    )

    async def _fake_resolve_approval(approval_id, approved, by):
        return _fake_result(tx_hash)

    monkeypatch.setattr(telegram_bot, "resolve_approval", _fake_resolve_approval)

    query = FakeQuery(OWNER_ID)
    query.data = "approve:42"
    update = FakeUpdate(OWNER_ID)
    update.callback_query = query

    await telegram_bot._handle_callback(update, FakeContext())

    assert custody_pause.is_paused() is False
    history = await kill_incident_log.list_incidents()
    assert len(history) == 1
    assert history[0]["event_type"] == "lifted"
    assert history[0]["tx_hash"] == tx_hash


@pytest.mark.asyncio
async def test_outflow_confirm_approve_stale_tx_never_lifts_a_different_incident(monkeypatch):
    """If ARIA got re-armed for a SECOND, unrelated incident in the meantime,
    a stale "Autorisé par moi" click on the FIRST alert must never lift the
    pause protecting against the second."""
    _set_owner(monkeypatch)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: True)
    stale_tx = "0xstale"
    fresh_tx = "0xfresh"
    custody_pause.pause(
        by="auto:agent_wallet_monitor",
        reason=f"Sortie non initiee par ARIA detectee automatiquement (wallet w, tx {fresh_tx})",
    )

    async def _fake_resolve_approval(approval_id, approved, by):
        return _fake_result(stale_tx)

    monkeypatch.setattr(telegram_bot, "resolve_approval", _fake_resolve_approval)

    query = FakeQuery(OWNER_ID)
    query.data = "approve:42"
    update = FakeUpdate(OWNER_ID)
    update.callback_query = query

    await telegram_bot._handle_callback(update, FakeContext())

    assert custody_pause.is_paused() is True  # still paused -- for the FRESH incident
    assert await kill_incident_log.list_incidents() == []


# --- End-to-end: kill really blocks execution, start really restores it --


@pytest.mark.asyncio
async def test_end_to_end_stop_blocks_swap_and_transfer_start_restores_them(monkeypatch, tmp_path):
    """Genuine integration test (operator request: "tu va faire un test en
    workflow pour voir si tu lance la commande kill si tout s'éteind... et
    lancer la commande start aussi"). Uses the REAL /stop and /resume
    handlers (real outgoing_pause.pause/resume, never mocked) and the REAL
    kill-switch check inside agent_wallet_pilot -- only the CDP-facing
    balance/swap/transfer functions are faked, so no real network call and
    no real money ever moves, but the pause/resume plumbing itself is
    exercised for real end to end."""
    from aria_core import agent_wallet_log, agent_wallet_pilot as pilot

    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "wallet_pilot_e2e_test.db"))
    _set_owner(monkeypatch)
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    monkeypatch.setenv("ARIA_AGENT_WALLET_TRANSFER_ENABLED", "true")

    async def _ok_balance() -> float:
        return 20.0

    async def _ok_swap(**kwargs) -> dict:
        return {"tx_hash": "0xdeadbeef", "amount_out": 0.001}

    async def _ok_transfer(**kwargs) -> dict:
        return {"tx_hash": "0xfeedface"}

    # 1. Clean slate: not paused, swap/transfer both go through.
    assert outgoing_pause.is_paused(strict=True) is False
    ok_swap = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert ok_swap.status == "ok"
    ok_transfer = await pilot.attempt_transfer(
        chain="base", to_address=pilot.ALLOWED_TRANSFER_ADDRESS, amount_usd=5.0,
        balance_fn=_ok_balance, transfer_fn=_ok_transfer,
    )
    assert ok_transfer.status == "ok"

    # 2. /stop (owner) -- real kill-switch armed.
    stop_update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_stop(stop_update, FakeContext())
    assert outgoing_pause.is_paused(strict=True) is True

    # 3. Both swap and transfer now blocked -- genuinely, via the same
    #    is_paused(strict=True) check agent_wallet_pilot already had.
    blocked_swap = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert blocked_swap.status == "blocked"
    blocked_transfer = await pilot.attempt_transfer(
        chain="base", to_address=pilot.ALLOWED_TRANSFER_ADDRESS, amount_usd=5.0,
        balance_fn=_ok_balance, transfer_fn=_ok_transfer,
    )
    assert blocked_transfer.status == "blocked"

    # 4. /resume (owner) -- real kill-switch lifted.
    resume_update = FakeUpdate(OWNER_ID)
    await telegram_bot._handle_resume(resume_update, FakeContext())
    assert outgoing_pause.is_paused(strict=True) is False

    # 5. Both swap and transfer work normally again.
    restored_swap = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert restored_swap.status == "ok"
    restored_transfer = await pilot.attempt_transfer(
        chain="base", to_address=pilot.ALLOWED_TRANSFER_ADDRESS, amount_usd=5.0,
        balance_fn=_ok_balance, transfer_fn=_ok_transfer,
    )
    assert restored_transfer.status == "ok"

    # 6. Both events (arm + lift) left their own trace -- no overwrite.
    history = await kill_incident_log.list_incidents()
    assert len(history) == 2
    assert {row["event_type"] for row in history} == {"armed", "lifted"}
