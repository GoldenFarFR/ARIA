"""Pool de tokens screenés + loterie (DB isolée, tirage déterministe via seed)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import screened_pool as sp


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "pool_test.db"))
    yield


async def _backdate(contract: str, hours_ago: float) -> None:
    """Recule ``last_checked_at`` -- simule un candidat pending laissé de côté depuis un moment."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    async with aiosqlite.connect(sp.DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET last_checked_at=? WHERE contract=?", (ts, contract)
        )
        await db.commit()


async def _backdate_first_screened(contract: str, days_ago: float) -> None:
    """Recule ``first_screened_at`` -- simule un candidat connu depuis longtemps."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    async with aiosqlite.connect(sp.DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET first_screened_at=? WHERE contract=?", (ts, contract)
        )
        await db.commit()


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
async def test_upsert_stores_source():
    await sp.upsert_screened(contract="0xsrc", symbol="S", liquidity_usd=1.0,
                             security_score=70, verdict="SAFE", source="top_pools")
    row = (await sp.list_pool())[0]
    assert row["source"] == "top_pools"


@pytest.mark.asyncio
async def test_upsert_default_source_is_empty_string():
    await sp.upsert_screened(contract="0xnosrc", symbol="N", liquidity_usd=1.0,
                             security_score=70, verdict="SAFE")
    row = (await sp.list_pool())[0]
    assert row["source"] == ""


@pytest.mark.asyncio
async def test_upsert_preserves_source_when_not_resupplied():
    await sp.upsert_screened(contract="0xkeep", symbol="K", liquidity_usd=1.0,
                             security_score=70, verdict="SAFE", source="radar_x")
    # Ré-enregistrement (ex. refresh périodique) sans repréciser la source : ne
    # doit PAS l'écraser à '' (première origine connue, censée être stable).
    await sp.upsert_screened(contract="0xkeep", symbol="K", liquidity_usd=2.0,
                             security_score=75, verdict="SAFE")
    row = (await sp.list_pool())[0]
    assert row["source"] == "radar_x"


@pytest.mark.asyncio
async def test_record_pending_stores_source():
    await sp.record_pending(contract="0xpend", reason="holders inconnus", source="radar_x")
    row = (await sp.list_pool(status="pending"))[0]
    assert row["source"] == "radar_x"


@pytest.mark.asyncio
async def test_record_rejected_stores_source():
    await sp.record_rejected(contract="0xrej", reason="honeypot", source="top_pools")
    row = (await sp.list_pool(status="rejected"))[0]
    assert row["source"] == "top_pools"


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


@pytest.mark.asyncio
async def test_list_stale_pending_excludes_fresh_entry():
    # Un pending qui vient d'être écrit (échec du crawl il y a quelques secondes) ne
    # doit pas être retenté tout de suite -- audit #77 : le retry existe pour les
    # candidats laissés de côté, pas pour spammer le même échec en boucle.
    await sp.record_pending(contract="0xfresh", reason="holders inconnus")
    assert await sp.list_stale_pending(older_than_hours=24) == []


@pytest.mark.asyncio
async def test_list_stale_pending_includes_old_entry():
    await sp.record_pending(contract="0xold", reason="contrat non vérifié")
    await _backdate("0xold", hours_ago=30)
    stale = await sp.list_stale_pending(older_than_hours=24)
    assert [row["contract"] for row in stale] == ["0xold"]


@pytest.mark.asyncio
async def test_list_stale_pending_excludes_active_and_rejected():
    await sp.upsert_screened(contract="0xactive", symbol="A", liquidity_usd=50_000.0,
                             security_score=80, verdict="SAFE")
    await sp.record_rejected(contract="0xrejected", reason="honeypot confirmé")
    for c in ("0xactive", "0xrejected"):
        await _backdate(c, hours_ago=48)
    assert await sp.list_stale_pending(older_than_hours=24) == []


@pytest.mark.asyncio
async def test_list_stale_pending_respects_limit_and_oldest_first():
    for i in range(5):
        await sp.record_pending(contract=f"0xp{i}", reason="pas encore mûr")
        await _backdate(f"0xp{i}", hours_ago=24 + i)  # 0xp4 = le plus vieux
    stale = await sp.list_stale_pending(older_than_hours=24, limit=2)
    assert [row["contract"] for row in stale] == ["0xp4", "0xp3"]


# --- retry_count : incrément / reset (suite audit #77/#105) -----------------------

@pytest.mark.asyncio
async def test_record_pending_increments_retry_count():
    await sp.record_pending(contract="0xretry", reason="holders inconnus")
    assert (await sp.list_pool(status="pending"))[0]["retry_count"] == 1
    await sp.record_pending(contract="0xretry", reason="holders inconnus")
    assert (await sp.list_pool(status="pending"))[0]["retry_count"] == 2


@pytest.mark.asyncio
async def test_upsert_screened_resets_retry_count():
    await sp.record_pending(contract="0xmature", reason="liquidité en train de monter")
    await sp.record_pending(contract="0xmature", reason="liquidité en train de monter")
    assert (await sp.list_pool(status="pending"))[0]["retry_count"] == 2
    await sp.upsert_screened(contract="0xmature", symbol="MAT", liquidity_usd=50_000.0,
                             security_score=80, verdict="SAFE")
    assert (await sp.list_pool(status="active"))[0]["retry_count"] == 0


@pytest.mark.asyncio
async def test_reconsider_resets_retry_count():
    await sp.record_rejected(contract="0xnoisy", reason="honeypot confirmé")
    assert await sp.reconsider("0xnoisy") is True
    row = (await sp.list_pool(status="pending"))[0]
    assert row["contract"] == "0xnoisy"
    assert row["retry_count"] == 0


# --- abandon_stale_pending : plafond tentatives / âge ------------------------------

@pytest.mark.asyncio
async def test_abandon_stale_pending_below_thresholds_is_noop():
    await sp.record_pending(contract="0xfresh2", reason="contrat pas encore vérifié")
    assert await sp.abandon_stale_pending("0xfresh2", max_retries=5, max_age_days=7) is False
    assert await sp.get_status("0xfresh2") == "pending"


@pytest.mark.asyncio
async def test_abandon_stale_pending_unknown_contract_is_noop():
    assert await sp.abandon_stale_pending("0xneverseen") is False


@pytest.mark.asyncio
async def test_abandon_stale_pending_ignores_non_pending_status():
    await sp.record_rejected(contract="0xalreadyrejected", reason="honeypot confirmé")
    assert await sp.abandon_stale_pending("0xalreadyrejected", max_retries=0, max_age_days=0) is False


@pytest.mark.asyncio
async def test_abandon_stale_pending_exceeds_max_retries():
    for _ in range(5):
        await sp.record_pending(contract="0xstuck", reason="score de sécurité 40 < 70")
    assert await sp.get_status("0xstuck") == "pending"
    assert await sp.abandon_stale_pending("0xstuck", max_retries=5, max_age_days=7) is True
    assert await sp.get_status("0xstuck") == "rejected"
    row = (await sp.list_pool(status="rejected"))[0]
    assert "abandonné après 5 tentatives" in row["screen_reason"]
    assert "score de sécurité 40 < 70" in row["screen_reason"]  # dernière raison molle gardée


@pytest.mark.asyncio
async def test_abandon_stale_pending_exceeds_max_age_even_with_few_retries():
    await sp.record_pending(contract="0xold2", reason="liquidité en train de monter")
    await _backdate_first_screened("0xold2", days_ago=8)
    assert await sp.abandon_stale_pending("0xold2", max_retries=5, max_age_days=7) is True
    assert await sp.get_status("0xold2") == "rejected"


@pytest.mark.asyncio
async def test_abandon_stale_pending_missing_reason_falls_back():
    await sp.record_pending(contract="0xnoreason")  # reason="" (défaut)
    for _ in range(4):
        await sp.record_pending(contract="0xnoreason")
    await sp.abandon_stale_pending("0xnoreason", max_retries=5, max_age_days=7)
    row = (await sp.list_pool(status="rejected"))[0]
    assert "raison indisponible" in row["screen_reason"]
