"""Classement "meilleurs investisseurs" (21/07) -- vérifie : gating,
update_leaderboard (no_percentile/not_eligible/added/updated/evicted_low_score/
evicted_capacity), capacité dure à 50, éviction sous 30, archivage, découverte
+ enfilement de candidats (triple gate, kill-switch)."""
from __future__ import annotations

import pytest

from aria_core.services import smart_money_leaderboard as lb

WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "DB_PATH", str(tmp_path / "smart_money_leaderboard_test.db"))
    yield


def test_disabled_by_default():
    assert lb.smart_money_leaderboard_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "1")
    assert lb.smart_money_leaderboard_enabled() is True


# ── update_leaderboard ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_none_percentile_is_a_noop():
    action = await lb.update_leaderboard(WALLET_A, None)
    assert action == "no_percentile"
    assert await lb.get_leaderboard() == []


@pytest.mark.asyncio
async def test_below_threshold_never_seen_is_not_eligible():
    action = await lb.update_leaderboard(WALLET_A, 15.0)
    assert action == "not_eligible"
    assert await lb.get_leaderboard() == []
    assert await lb.get_archive() == []  # jamais archivé -- n'a jamais été dans le classement


@pytest.mark.asyncio
async def test_first_real_score_added():
    action = await lb.update_leaderboard(WALLET_A, 72.0)
    assert action == "added"
    rows = await lb.get_leaderboard()
    assert len(rows) == 1
    assert rows[0]["wallet"] == WALLET_A.lower()
    assert rows[0]["composite_percentile"] == 72.0
    assert rows[0]["rank"] == 1


@pytest.mark.asyncio
async def test_rescoring_updates_existing_entry():
    await lb.update_leaderboard(WALLET_A, 72.0)
    action = await lb.update_leaderboard(WALLET_A, 90.0)
    assert action == "updated"
    rows = await lb.get_leaderboard()
    assert len(rows) == 1
    assert rows[0]["composite_percentile"] == 90.0


@pytest.mark.asyncio
async def test_rank_order_by_percentile_desc():
    await lb.update_leaderboard(WALLET_A, 40.0)
    await lb.update_leaderboard(WALLET_B, 90.0)
    rows = await lb.get_leaderboard()
    assert [r["wallet"] for r in rows] == [WALLET_B.lower(), WALLET_A.lower()]
    assert [r["rank"] for r in rows] == [1, 2]


@pytest.mark.asyncio
async def test_wallet_already_in_leaderboard_dropping_below_30_is_evicted_and_archived():
    await lb.update_leaderboard(WALLET_A, 60.0)
    action = await lb.update_leaderboard(WALLET_A, 20.0)
    assert action == "evicted_low_score"
    assert await lb.get_leaderboard() == []
    archive = await lb.get_archive()
    assert len(archive) == 1
    assert archive[0]["wallet"] == WALLET_A.lower()
    assert archive[0]["percentile_at_removal"] == 20.0
    assert archive[0]["reason"] == "percentile sous 30"


@pytest.mark.asyncio
async def test_capacity_evicts_lowest_percentile_beyond_50():
    for i in range(50):
        await lb.update_leaderboard(f"0x{i:040d}", 50.0 + i)  # 50..99
    rows = await lb.get_leaderboard()
    assert len(rows) == 50

    action = await lb.update_leaderboard("0x" + "f" * 40, 200.0)  # nouveau plus haut que tous
    assert action == "added"
    rows = await lb.get_leaderboard()
    assert len(rows) == 50  # toujours plafonné à 50
    lowest_wallet = f"0x{0:040d}"  # percentile 50.0, le plus bas du lot initial
    assert not any(r["wallet"] == lowest_wallet for r in rows)

    archive = await lb.get_archive()
    reasons = {a["wallet"]: a["reason"] for a in archive}
    assert reasons[lowest_wallet] == "hors du top 50 (capacité)"


@pytest.mark.asyncio
async def test_capacity_eviction_can_evict_the_wallet_just_added():
    for i in range(50):
        await lb.update_leaderboard(f"0x{i:040d}", 50.0 + i)  # 50..99, le pire à 50.0

    # Un score qui rejoint le classement mais reste sous tous les autres --
    # doit repartir immédiatement (évincé par sa propre capacité).
    action = await lb.update_leaderboard("0x" + "e" * 40, 40.0)
    assert action == "evicted_capacity"
    rows = await lb.get_leaderboard()
    assert not any(r["wallet"] == ("0x" + "e" * 40) for r in rows)


@pytest.mark.asyncio
async def test_empty_wallet_is_noop():
    assert await lb.update_leaderboard("", 80.0) == "no_percentile"
    assert await lb.update_leaderboard("   ", 80.0) == "no_percentile"


# ── discover_and_enqueue_candidates (triple gate + kill-switch) ─────────────

def _enable_all(monkeypatch):
    monkeypatch.setenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCAN_QUEUE_ENABLED", "1")
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "1")


@pytest.mark.asyncio
async def test_discovery_skipped_when_own_gate_off():
    result = await lb.discover_and_enqueue_candidates()
    assert result == {"outcome": "skipped", "reason": "gate_off"}


@pytest.mark.asyncio
async def test_discovery_skipped_when_downstream_gate_off(monkeypatch):
    monkeypatch.setenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "1")
    result = await lb.discover_and_enqueue_candidates()
    assert result == {"outcome": "skipped", "reason": "downstream_disabled"}


@pytest.mark.asyncio
async def test_discovery_respects_kill_switch(monkeypatch, tmp_path):
    _enable_all(monkeypatch)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await lb.discover_and_enqueue_candidates()
    assert result == {"outcome": "skipped", "reason": "paused"}


@pytest.mark.asyncio
async def test_discovery_enqueues_real_candidates(monkeypatch, tmp_path):
    _enable_all(monkeypatch)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    monkeypatch.setattr(
        "aria_core.services.wallet_scan_queue.DB_PATH", str(tmp_path / "wallet_scan_queue_test.db")
    )
    from aria_core import token_holder_intel

    monkeypatch.setattr(token_holder_intel, "DB_PATH", str(tmp_path / "token_holder_intel_test.db"))
    for contract in ("0xTOKEN_A", "0xTOKEN_B", "0xTOKEN_C"):
        await token_holder_intel.store_holders(
            contract, "base",
            [{
                "holder_address": WALLET_A, "holder_name": None, "is_contract": False,
                "is_verified": False, "is_scam": False, "reputation": None, "tags": [], "value": "1",
            }],
        )

    result = await lb.discover_and_enqueue_candidates()
    assert result["outcome"] == "ok"
    assert result["candidates_found"] == 1
    assert result["added_to_queue"] == 1

    from aria_core.services import wallet_scan_queue

    assert await wallet_scan_queue.queue_size() == 1


@pytest.mark.asyncio
async def test_discovery_no_candidate_found(monkeypatch, tmp_path):
    _enable_all(monkeypatch)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    monkeypatch.setattr(
        "aria_core.services.wallet_scan_queue.DB_PATH", str(tmp_path / "wallet_scan_queue_test.db")
    )
    from aria_core import token_holder_intel

    monkeypatch.setattr(token_holder_intel, "DB_PATH", str(tmp_path / "token_holder_intel_test.db"))

    result = await lb.discover_and_enqueue_candidates()
    assert result == {"outcome": "no_candidate"}
