"""Item #236 (30/07, operator request: /add) -- manually-queued discovery
candidates. Persisted (survives redeployments, same doctrine as
momentum_blacklist.py), expires after MANUAL_CANDIDATE_TTL_DAYS if never
consumed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import manual_candidates as mc
from aria_core.paths import aria_db_path


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "manual_candidates_test.db")
    monkeypatch.setattr(mc, "DB_PATH", db_path)
    yield


@pytest.mark.asyncio
async def test_empty_queue_by_default():
    assert await mc.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_add_then_list_returns_it():
    assert await mc.add_manual_candidate("0xABC", "base") is True
    pending = await mc.list_pending_manual_candidates()
    assert len(pending) == 1
    assert pending[0]["contract"] == "0xabc"
    assert pending[0]["chain"] == "base"


@pytest.mark.asyncio
async def test_defaults_to_base_chain():
    await mc.add_manual_candidate("0xABC")
    pending = await mc.list_pending_manual_candidates()
    assert pending[0]["chain"] == "base"


@pytest.mark.asyncio
async def test_solana_case_preserved_base_lowercased():
    await mc.add_manual_candidate("0xABC", "base")
    await mc.add_manual_candidate("SoLMixedCase", "solana")
    pending = await mc.list_pending_manual_candidates()
    contracts = {p["contract"] for p in pending}
    assert "0xabc" in contracts
    assert "SoLMixedCase" in contracts


@pytest.mark.asyncio
async def test_empty_contract_or_chain_is_a_no_op():
    assert await mc.add_manual_candidate("", "base") is False
    assert await mc.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_duplicate_submission_keeps_original_added_at():
    await mc.add_manual_candidate("0xABC", "base")
    first = (await mc.list_pending_manual_candidates())[0]["added_at"]
    await mc.add_manual_candidate("0xABC", "base")
    second = (await mc.list_pending_manual_candidates())[0]["added_at"]
    assert first == second
    assert len(await mc.list_pending_manual_candidates()) == 1


@pytest.mark.asyncio
async def test_expired_entry_is_purged_on_list(monkeypatch):
    await mc.add_manual_candidate("0xABC", "base")
    future = datetime.now(timezone.utc) + timedelta(days=mc.MANUAL_CANDIDATE_TTL_DAYS + 1)

    real_datetime = mc.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(mc, "datetime", _FrozenDatetime)
    assert await mc.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_remove_manual_candidate_deletes_it():
    await mc.add_manual_candidate("0xABC", "base")
    await mc.remove_manual_candidate("0xABC", "base")
    assert await mc.list_pending_manual_candidates() == []


@pytest.mark.asyncio
async def test_remove_unknown_candidate_is_a_no_op():
    await mc.remove_manual_candidate("0xNEVERADDED", "base")
    assert await mc.list_pending_manual_candidates() == []


# ── GoPlus watchlist queueing (Item #241, 30/07, operator request: ──────────
# "je veux juste qu'ils entrent rapidement dans la watchlist")

async def _watchlist_row(contract: str, chain: str):
    async with aiosqlite.connect(str(aria_db_path())) as db:
        return await (
            await db.execute(
                "SELECT priority_score FROM goplus_watchlist WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()


@pytest.mark.asyncio
async def test_add_manual_candidate_also_queues_goplus_watchlist():
    from aria_core.services import goplus_watchlist

    await mc.add_manual_candidate("0xABC", "base")

    security = await goplus_watchlist.get_fresh("0xabc", "base")
    # Not yet security-checked (no honeypot data written) -- but the SLOT
    # exists, which is the whole point: it's queued for the background cycle.
    assert security is None
    assert await _watchlist_row("0xabc", "base") is not None


@pytest.mark.asyncio
async def test_add_manual_candidate_uses_real_liquidity_for_priority_score():
    from aria_core.services import goplus_watchlist

    await mc.add_manual_candidate("0xABC", "base", liquidity_usd=500_000.0, volume_24h_usd=200_000.0)

    expected_score = goplus_watchlist.compute_priority_score(500_000.0, 200_000.0)
    row = await _watchlist_row("0xabc", "base")
    assert row is not None
    assert row[0] == pytest.approx(expected_score)
    assert expected_score > 0.0  # sanity: real data must produce a non-trivial score


@pytest.mark.asyncio
async def test_add_manual_candidate_defaults_to_neutral_score_without_liquidity_data():
    """The bare /add path (no DexScreener fetch performed) still gets a slot
    -- a neutral 0.0 score, which claims a slot as long as the watchlist
    isn't full (never blocks fast entry for lack of extra data)."""
    await mc.add_manual_candidate("0xABC", "base")

    row = await _watchlist_row("0xabc", "base")
    assert row is not None
    assert row[0] == 0.0


@pytest.mark.asyncio
async def test_add_manual_candidate_tolerates_watchlist_queueing_failure(monkeypatch):
    from aria_core.services import goplus_watchlist

    async def raising(contract, chain, score):
        raise RuntimeError("boom")

    monkeypatch.setattr(goplus_watchlist, "add_or_touch", raising)

    # The discovery-queue insert must still succeed even if the watchlist
    # queueing fails.
    assert await mc.add_manual_candidate("0xABC", "base") is True
    pending = await mc.list_pending_manual_candidates()
    assert len(pending) == 1
