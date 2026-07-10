"""Pool de tokens screenés + loterie (DB isolée, tirage déterministe via seed)."""
from __future__ import annotations

import random

import pytest

from aria_core import screened_pool as sp


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "pool_test.db"))
    yield


async def _seed_pool(n: int) -> None:
    for i in range(n):
        await sp.upsert_screened(
            contract=f"0x{i:040x}", symbol=f"T{i}", liquidity_usd=50_000.0 + i,
            security_score=75, top_holder_pct=12.0, verdict="SAFE",
            pool_address=f"0xpool{i}",
        )


@pytest.mark.asyncio
async def test_upsert_and_count():
    await _seed_pool(3)
    assert await sp.count_pool() == 3
    pool = await sp.list_pool()
    assert {p["symbol"] for p in pool} == {"T0", "T1", "T2"}


@pytest.mark.asyncio
async def test_upsert_same_contract_no_duplicate():
    await sp.upsert_screened(contract="0xabc", symbol="A", liquidity_usd=1.0,
                             security_score=70, verdict="SAFE")
    await sp.upsert_screened(contract="0xabc", symbol="A2", liquidity_usd=2.0,
                             security_score=80, verdict="SAFE")
    assert await sp.count_pool() == 1
    row = (await sp.list_pool())[0]
    assert row["symbol"] == "A2"  # rafraîchi
    assert row["security_score"] == 80


@pytest.mark.asyncio
async def test_drop_removes_from_active():
    await _seed_pool(2)
    await sp.drop_token("0x" + "0" * 40)  # T0
    assert await sp.count_pool("active") == 1
    assert await sp.count_pool("dropped") == 1


@pytest.mark.asyncio
async def test_record_pending_stores_reason_without_blocking_rescan():
    await sp.record_pending(contract="0xsoft", symbol="SOFT", reason="holders inconnus")
    assert await sp.get_status("0xsoft") == "pending"
    row = (await sp.list_pool(status="pending"))[0]
    assert row["screen_reason"] == "holders inconnus"
    # 'pending' n'est ni 'active' ni 'rejected' : rien dans le pool actif compte.
    assert await sp.count_pool("active") == 0


@pytest.mark.asyncio
async def test_lottery_draws_distinct_subset():
    await _seed_pool(10)
    random.seed(0)
    picks = await sp.draw_lottery(3)
    assert len(picks) == 3
    assert len({p["contract"] for p in picks}) == 3  # distincts


@pytest.mark.asyncio
async def test_lottery_returns_all_when_pool_smaller():
    await _seed_pool(4)
    picks = await sp.draw_lottery(20)
    assert len(picks) == 4


@pytest.mark.asyncio
async def test_lottery_empty_pool():
    assert await sp.draw_lottery(20) == []


@pytest.mark.asyncio
async def test_bonding_pool_isolated_from_vc_pool():
    """network='base-bonding' (niche 15%) ne doit JAMAIS apparaître dans le tirage
    85% VC par défaut (network='base') — sinon contamination du track-record."""
    await _seed_pool(5)  # tout en network="base" (défaut)
    await sp.upsert_screened(
        contract="0xbond1", symbol="BOND1", liquidity_usd=0.0,
        security_score=60, verdict="SAFE", network="base-bonding",
    )
    assert await sp.count_pool("active") == 5
    assert await sp.count_pool("active", network="base-bonding") == 1
    vc_pool = await sp.list_pool()
    assert "BOND1" not in {p["symbol"] for p in vc_pool}
    bonding_pool = await sp.list_pool(network="base-bonding")
    assert {p["symbol"] for p in bonding_pool} == {"BOND1"}
    vc_draw = await sp.draw_lottery(20)
    assert "BOND1" not in {p["symbol"] for p in vc_draw}
    bonding_draw = await sp.draw_lottery(20, network="base-bonding")
    assert {p["symbol"] for p in bonding_draw} == {"BOND1"}
