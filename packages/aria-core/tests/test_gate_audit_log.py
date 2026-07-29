"""Item #188 (29/07) -- timestamped audit trail for the critical capital-
adjacent gates. DB isolée par test (même patron que test_momentum_scan_log.py),
aucun appel réseau."""
from __future__ import annotations

import pytest

from aria_core import gate_audit_log as gal


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(gal, "DB_PATH", str(tmp_path / "gate_audit_test.db"))
    gal._last_known_state.clear()
    yield
    gal._last_known_state.clear()


@pytest.mark.asyncio
async def test_first_check_always_records():
    await gal.record_gate_transition_if_changed("GATE_A", True)
    history = await gal.list_history("GATE_A")
    assert len(history) == 1
    assert history[0]["enabled"] == 1


@pytest.mark.asyncio
async def test_repeated_same_value_records_once():
    await gal.record_gate_transition_if_changed("GATE_A", True)
    await gal.record_gate_transition_if_changed("GATE_A", True)
    await gal.record_gate_transition_if_changed("GATE_A", True)
    history = await gal.list_history("GATE_A")
    assert len(history) == 1


@pytest.mark.asyncio
async def test_real_transition_records_new_row():
    await gal.record_gate_transition_if_changed("GATE_A", True)
    await gal.record_gate_transition_if_changed("GATE_A", False)
    history = await gal.list_history("GATE_A")
    assert len(history) == 2
    assert history[0]["enabled"] == 0  # most recent first
    assert history[1]["enabled"] == 1


@pytest.mark.asyncio
async def test_different_gates_tracked_independently():
    await gal.record_gate_transition_if_changed("GATE_A", True)
    await gal.record_gate_transition_if_changed("GATE_B", False)
    assert len(await gal.list_history("GATE_A")) == 1
    assert len(await gal.list_history("GATE_B")) == 1


@pytest.mark.asyncio
async def test_fresh_process_reads_last_db_state_before_deciding():
    """Item #188's own stated guarantee: a process restart must not re-log a
    spurious transition for a gate that never actually changed -- the
    in-memory cache is empty on a fresh process, so the DB's own last row
    must be consulted first."""
    await gal.record_gate_transition_if_changed("GATE_A", True)
    gal._last_known_state.clear()  # simulates a fresh process (cache lost, DB persists)
    await gal.record_gate_transition_if_changed("GATE_A", True)
    history = await gal.list_history("GATE_A")
    assert len(history) == 1  # no spurious duplicate


@pytest.mark.asyncio
async def test_state_at_reconstructs_past_value():
    from datetime import datetime, timedelta, timezone

    await gal.record_gate_transition_if_changed("GATE_A", True)
    mid = datetime.now(timezone.utc)
    await gal.record_gate_transition_if_changed("GATE_A", False)

    assert await gal.state_at("GATE_A", mid) is True
    assert await gal.state_at("GATE_A", datetime.now(timezone.utc)) is False
    assert await gal.state_at("GATE_A", mid - timedelta(hours=1)) is None


@pytest.mark.asyncio
async def test_snapshot_tracked_gates_covers_all_four(monkeypatch):
    from aria_core import agent_wallet_pilot, x402_seller

    monkeypatch.setattr(x402_seller, "seller_enabled", lambda: True)
    monkeypatch.setattr(x402_seller, "seller_mainnet_enabled", lambda: False)
    monkeypatch.setattr(agent_wallet_pilot, "agent_wallet_pilot_enabled", lambda: True)
    monkeypatch.setattr(agent_wallet_pilot, "agent_wallet_transfer_enabled", lambda: False)

    await gal.snapshot_tracked_gates()

    for gate_name in gal.TRACKED_GATES:
        history = await gal.list_history(gate_name)
        assert len(history) == 1, f"{gate_name} should have exactly one recorded state"


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    """Best-effort telemetry -- a write failure must never propagate to the
    caller's own cycle."""
    monkeypatch.setattr(gal, "_ensure_table", None)  # will raise TypeError if called

    async def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(gal, "_ensure_table", _boom)
    await gal.record_gate_transition_if_changed("GATE_A", True)  # must not raise
