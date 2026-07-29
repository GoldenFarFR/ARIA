"""Item #198 (29/07) -- append-only incident log for every kill-switch event
(arm/lift, manual/auto). Isolated DB per test (same pattern as
test_gate_audit_log.py), no network call."""
from __future__ import annotations

import pytest

from aria_core import kill_incident_log as kil


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kil, "DB_PATH", str(tmp_path / "kill_incident_test.db"))
    yield


@pytest.mark.asyncio
async def test_record_and_list_one_incident():
    await kil.record_incident(
        event_type=kil.EVENT_ARMED, trigger_source=kil.TRIGGER_AUTO,
        by="auto:agent_wallet_monitor", reason="test", wallet_name="w1", tx_hash="0xabc",
    )
    history = await kil.list_incidents()
    assert len(history) == 1
    assert history[0]["event_type"] == "armed"
    assert history[0]["trigger_source"] == "auto"
    assert history[0]["wallet_name"] == "w1"
    assert history[0]["tx_hash"] == "0xabc"
    assert history[0]["by"] == "auto:agent_wallet_monitor"


@pytest.mark.asyncio
async def test_every_incident_keeps_its_own_row_never_overwritten():
    """The exact gap this module closes: unlike pause_state.json (a single
    overwritten snapshot), each incident must survive the NEXT one."""
    await kil.record_incident(event_type=kil.EVENT_ARMED, trigger_source=kil.TRIGGER_AUTO, tx_hash="0x1")
    await kil.record_incident(event_type=kil.EVENT_LIFTED, trigger_source=kil.TRIGGER_MANUAL, tx_hash="0x1")
    await kil.record_incident(event_type=kil.EVENT_ARMED, trigger_source=kil.TRIGGER_AUTO, tx_hash="0x2")

    history = await kil.list_incidents()
    assert len(history) == 3
    assert [row["tx_hash"] for row in history] == ["0x2", "0x1", "0x1"]  # most recent first


@pytest.mark.asyncio
async def test_limit_is_respected():
    for i in range(5):
        await kil.record_incident(event_type=kil.EVENT_ARMED, trigger_source=kil.TRIGGER_AUTO, tx_hash=f"0x{i}")
    history = await kil.list_incidents(limit=2)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    """Best-effort telemetry -- a write failure must never propagate to the
    caller's own pause/resume."""
    async def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(kil, "_ensure_table", _boom)
    await kil.record_incident(event_type=kil.EVENT_ARMED, trigger_source=kil.TRIGGER_AUTO)  # must not raise
