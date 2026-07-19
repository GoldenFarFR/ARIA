"""Sealed Ledger (19/07, #214) -- chaînage crypto, VWAP/slippage, re-vérification tierce.
Voir sealed_ledger.py pour le design complet."""
from __future__ import annotations

import pytest
import aiosqlite

from aria_core import sealed_ledger as sl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "DB_PATH", str(tmp_path / "sealed_ledger_test.db"))
    yield


# ── JSON canonique ───────────────────────────────────────────────────────────────────

def test_canonical_json_deterministic_regardless_of_key_order():
    a = {"z": 1, "a": 2, "m": {"y": 1, "b": 2}}
    b = {"a": 2, "m": {"b": 2, "y": 1}, "z": 1}
    assert sl.canonical_json(a) == sl.canonical_json(b)


def test_canonical_json_has_no_whitespace():
    out = sl.canonical_json({"a": 1, "b": 2})
    assert " " not in out


# ── Chaînage de base ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_event_chains_to_genesis():
    ev = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    assert ev.prev_hash == sl.GENESIS_HASH
    assert ev.sequence == 1
    assert len(ev.hash) == 64


@pytest.mark.asyncio
async def test_second_event_chains_to_first():
    ev1 = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    ev2 = await sl.record_entry_fill(
        trade_id="t1", entry_decision_hash=ev1.hash,
        execution_price_usd=1.0, filled_quantity=100.0,
    )
    assert ev2.prev_hash == ev1.hash
    assert ev2.sequence == 2


@pytest.mark.asyncio
async def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        await sl._append_event(trade_id="t1", event_type="NOT_A_TYPE", payload={})


@pytest.mark.asyncio
async def test_invalid_fill_status_rejected():
    with pytest.raises(ValueError):
        await sl.record_entry_fill(
            trade_id="t1", entry_decision_hash="x",
            execution_price_usd=1.0, filled_quantity=1.0, fill_status="BOGUS",
        )


@pytest.mark.asyncio
async def test_exit_fill_sequence_index_must_start_at_one():
    with pytest.raises(ValueError):
        await sl.record_exit_fill(
            trade_id="t1", exit_decision_hash="x", sequence_index=0,
            execution_price_usd=1.0, filled_quantity=1.0, fill_status="FINAL",
        )


# ── Garde-fou append-only (trigger SQLite dur) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_update_is_rejected_by_trigger():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    async with aiosqlite.connect(sl.DB_PATH) as db:
        with pytest.raises(aiosqlite.IntegrityError, match="append-only"):
            await db.execute("UPDATE sealed_ledger_events SET trade_id = 'hacked'")
            await db.commit()


@pytest.mark.asyncio
async def test_delete_is_rejected_by_trigger():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    async with aiosqlite.connect(sl.DB_PATH) as db:
        with pytest.raises(aiosqlite.IntegrityError, match="append-only"):
            await db.execute("DELETE FROM sealed_ledger_events")
            await db.commit()


# ── VWAP / slippage ──────────────────────────────────────────────────────────────────

def test_vwap_single_fill_equals_its_price():
    assert sl.compute_vwap([{"execution_price_usd": 2.0, "filled_quantity": 50.0}]) == 2.0


def test_vwap_weighted_across_multiple_fills():
    fills = [
        {"execution_price_usd": 1.0, "filled_quantity": 100.0},
        {"execution_price_usd": 3.0, "filled_quantity": 100.0},
    ]
    assert sl.compute_vwap(fills) == 2.0


def test_vwap_empty_fills_is_zero_not_a_crash():
    assert sl.compute_vwap([]) == 0.0


def test_slippage_bps_positive_when_fill_above_decision():
    bps = sl.compute_slippage_bps(vwap_fills=1.05, decision_price_usd=1.0)
    assert bps == pytest.approx(500.0)


def test_slippage_bps_negative_when_fill_below_decision():
    bps = sl.compute_slippage_bps(vwap_fills=0.95, decision_price_usd=1.0)
    assert bps == pytest.approx(-500.0)


def test_slippage_bps_none_when_decision_price_invalid():
    assert sl.compute_slippage_bps(vwap_fills=1.0, decision_price_usd=0.0) is None
    assert sl.compute_slippage_bps(vwap_fills=1.0, decision_price_usd=-1.0) is None


# ── Cycle de vie complet d'un trade ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_trade_lifecycle_single_fill_closed():
    entry = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_fill(
        trade_id="t1", entry_decision_hash=entry.hash,
        execution_price_usd=1.0, filled_quantity=100.0,
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="t1", entry_decision_hash=entry.hash,
        decision_price_usd=1.5, target_quantity=100.0, exit_reason="take-profit",
    )
    await sl.record_exit_fill(
        trade_id="t1", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=1.5, filled_quantity=100.0, fill_status="FINAL",
    )

    result = await sl.compute_trade_pnl("t1")
    assert result["status"] == "CLOSED"
    assert result["pnl_usd"] == pytest.approx(50.0)
    assert result["pnl_pct"] == pytest.approx(50.0)
    assert result["entry_slippage_bps"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_open_trade_has_no_exit_fields():
    entry = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_fill(
        trade_id="t1", entry_decision_hash=entry.hash,
        execution_price_usd=1.0, filled_quantity=100.0,
    )
    result = await sl.compute_trade_pnl("t1")
    assert result["status"] == "OPEN"
    assert "pnl_usd" not in result


@pytest.mark.asyncio
async def test_partial_exit_multiple_fills_vwap_and_status():
    entry = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_fill(
        trade_id="t1", entry_decision_hash=entry.hash,
        execution_price_usd=1.0, filled_quantity=100.0,
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="t1", entry_decision_hash=entry.hash,
        decision_price_usd=1.2, target_quantity=100.0, exit_reason="stop",
    )
    # Liquidité fragmentée : deux fills à des prix différents avant d'atteindre la cible.
    await sl.record_exit_fill(
        trade_id="t1", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=1.1, filled_quantity=40.0, fill_status="PARTIAL",
    )
    result_partial = await sl.compute_trade_pnl("t1")
    assert result_partial["status"] == "PARTIAL"

    await sl.record_exit_fill(
        trade_id="t1", exit_decision_hash=exit_dec.hash, sequence_index=2,
        execution_price_usd=0.9, filled_quantity=60.0, fill_status="FINAL",
    )
    result_final = await sl.compute_trade_pnl("t1")
    assert result_final["status"] == "CLOSED"
    # VWAP = (1.1*40 + 0.9*60) / 100 = 0.98
    assert result_final["exit_vwap_usd"] == pytest.approx(0.98)


@pytest.mark.asyncio
async def test_exit_abandoned_never_values_the_remainder():
    entry = await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_fill(
        trade_id="t1", entry_decision_hash=entry.hash,
        execution_price_usd=1.0, filled_quantity=100.0,
    )
    exit_dec = await sl.record_exit_decision(
        trade_id="t1", entry_decision_hash=entry.hash,
        decision_price_usd=0.5, target_quantity=100.0, exit_reason="liquidité disparue",
    )
    await sl.record_exit_fill(
        trade_id="t1", exit_decision_hash=exit_dec.hash, sequence_index=1,
        execution_price_usd=0.5, filled_quantity=30.0, fill_status="PARTIAL",
    )
    await sl.record_exit_abandoned(
        trade_id="t1", exit_decision_hash=exit_dec.hash,
        remaining_quantity=70.0, reason="pool vidée",
    )
    result = await sl.compute_trade_pnl("t1")
    assert result["status"] == "ABANDONED"
    assert result["abandoned_quantity"] == 70.0
    # PnL calculé UNIQUEMENT sur les 30 unités réellement sorties, jamais sur les 70 figées.
    assert result["filled_quantity"] == 30.0


# ── Re-vérification tierce (verify_chain) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_chain_accepts_untampered_events():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_decision(
        trade_id="t2", token_address="0xdef", chain="base",
        decision_price_usd=2.0, target_size_usd=50.0, thesis="test 2",
        conviction=60, pipeline="vc", source_price="dexscreener:pool2",
    )
    events = await sl.list_events()
    ok, reason = sl.verify_chain(events)
    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_verify_chain_detects_tampered_payload():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    events = await sl.list_events()
    events[0]["payload"]["decision_price_usd"] = 999.0  # falsification après coup

    ok, reason = sl.verify_chain(events)
    assert ok is False
    assert "falsifié" in reason


@pytest.mark.asyncio
async def test_verify_chain_detects_broken_prev_hash_link():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_decision(
        trade_id="t2", token_address="0xdef", chain="base",
        decision_price_usd=2.0, target_size_usd=50.0, thesis="test 2",
        conviction=60, pipeline="vc", source_price="dexscreener:pool2",
    )
    events = await sl.list_events()
    events[1]["prev_hash"] = "f" * 64  # chaînage cassé

    ok, reason = sl.verify_chain(events)
    assert ok is False
    assert "chaînage rompu" in reason


@pytest.mark.asyncio
async def test_verify_chain_detects_sequence_gap():
    await sl.record_entry_decision(
        trade_id="t1", token_address="0xabc", chain="base",
        decision_price_usd=1.0, target_size_usd=100.0, thesis="test",
        conviction=80, pipeline="momentum", source_price="dexscreener:pool1",
    )
    await sl.record_entry_decision(
        trade_id="t2", token_address="0xdef", chain="base",
        decision_price_usd=2.0, target_size_usd=50.0, thesis="test 2",
        conviction=60, pipeline="vc", source_price="dexscreener:pool2",
    )
    events = await sl.list_events()
    events[1]["sequence"] = 5

    ok, reason = sl.verify_chain(events)
    assert ok is False
    assert "séquence rompue" in reason


def test_verify_chain_empty_list_is_trivially_valid():
    ok, reason = sl.verify_chain([])
    assert ok is True
    assert reason is None


def test_verify_chain_does_not_touch_the_database():
    """Fonction pure -- vérifiable en isolation totale, aucune connexion DB requise
    (c'est la propriété qui rend la re-vérification tierce réellement indépendante)."""
    hand_built = {
        "event_id": "e1", "trade_id": "t1", "event_type": "ENTRY_DECISION",
        "sequence": 1, "timestamp_utc": "2026-07-19T00:00:00+00:00",
        "prev_hash": sl.GENESIS_HASH, "payload": {"x": 1},
    }
    hand_built["hash"] = sl._compute_event_hash(
        event_id=hand_built["event_id"], trade_id=hand_built["trade_id"],
        event_type=hand_built["event_type"], sequence=hand_built["sequence"],
        timestamp_utc=hand_built["timestamp_utc"], prev_hash=hand_built["prev_hash"],
        payload=hand_built["payload"],
    )
    ok, reason = sl.verify_chain([hand_built])
    assert ok is True
