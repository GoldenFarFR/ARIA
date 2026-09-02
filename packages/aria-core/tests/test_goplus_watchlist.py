"""GoPlus honeypot watchlist (Item #212, 29/07) -- DB isolated per test via the
global ``_isolated_runtime`` fixture (``aria_db_path()`` is resolved dynamically
inside every function here, never frozen at import time -- same fix already
applied to ``blockscout_credit_budget.py``/``tavily_budget.py``)."""
from __future__ import annotations

import json

import pytest

from aria_core.services import goplus_watchlist as wl
from aria_core.services.goplus import TokenSecurity

CONTRACT_A = "0x" + "a" * 40
CONTRACT_B = "0x" + "b" * 40
CONTRACT_C = "0x" + "c" * 40


def _security(**overrides) -> TokenSecurity:
    base = dict(address=CONTRACT_A, is_honeypot=False, cannot_sell_all=False, available=True)
    base.update(overrides)
    return TokenSecurity(**base)


# ── compute_priority_score ──────────────────────────────────────────────

def test_priority_score_higher_liquidity_and_activity_scores_higher():
    low = wl.compute_priority_score(15_000, 1_000)
    high = wl.compute_priority_score(500_000, 400_000)
    assert high > low


def test_priority_score_below_liquidity_floor_is_zero_liquidity_component():
    # liquidity < 10k contributes nothing to the liquidity pillar -- only
    # activity (volume/liquidity ratio) can still contribute.
    score = wl.compute_priority_score(5_000, 5_000)
    assert score == pytest.approx(30.0)  # ratio=1.0 -> activity capped at 30


def test_priority_score_never_crashes_on_none_or_zero():
    assert wl.compute_priority_score(None, None) == 0.0
    assert wl.compute_priority_score(0, 0) == 0.0


# ── add_or_touch / eviction ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_or_touch_adds_new_candidate():
    added = await wl.add_or_touch(CONTRACT_A, "base", 50.0)
    assert added is True
    assert await wl.count() == 1


@pytest.mark.asyncio
async def test_add_or_touch_updates_score_of_existing_candidate():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await wl.add_or_touch(CONTRACT_A, "base", 90.0)
    assert await wl.count() == 1
    rows = await wl.list_all()
    assert rows[0]["priority_score"] == 90.0


@pytest.mark.asyncio
async def test_add_or_touch_stores_links_for_new_candidate():
    """09/08 -- links now stored so the signal cascade can enqueue straight
    from this watchlist (explicit operator instruction, see
    docs/HANDOFF_SIGNAL_CASCADE.md)."""
    links = [{"label": "Website", "url": "https://example.com"}]
    await wl.add_or_touch(CONTRACT_A, "base", 50.0, links=links)
    rows = await wl.list_all()
    assert json.loads(rows[0]["links_json"]) == links


@pytest.mark.asyncio
async def test_add_or_touch_without_links_stores_none():
    await wl.add_or_touch(CONTRACT_A, "base", 50.0)
    rows = await wl.list_all()
    assert rows[0]["links_json"] is None


@pytest.mark.asyncio
async def test_add_or_touch_touch_never_erases_stored_links_with_none():
    """A later touch call with no links (e.g. a caller that never resolved
    them) must never wipe out an already-stored value -- COALESCE, not a
    blind overwrite."""
    links = [{"label": "Website", "url": "https://example.com"}]
    await wl.add_or_touch(CONTRACT_A, "base", 10.0, links=links)
    await wl.add_or_touch(CONTRACT_A, "base", 90.0)  # touch, no links this time
    rows = await wl.list_all()
    assert json.loads(rows[0]["links_json"]) == links
    assert rows[0]["priority_score"] == 90.0


@pytest.mark.asyncio
async def test_add_or_touch_touch_updates_links_when_provided():
    old_links = [{"label": "Website", "url": "https://old.example.com"}]
    new_links = [{"label": "Website", "url": "https://new.example.com"}]
    await wl.add_or_touch(CONTRACT_A, "base", 10.0, links=old_links)
    await wl.add_or_touch(CONTRACT_A, "base", 20.0, links=new_links)
    rows = await wl.list_all()
    assert json.loads(rows[0]["links_json"]) == new_links


@pytest.mark.asyncio
async def test_watchlist_full_evicts_worst_when_new_score_is_better(monkeypatch):
    monkeypatch.setattr(wl, "MAX_WATCHLIST_SIZE", 2)
    assert await wl.add_or_touch(CONTRACT_A, "base", 10.0) is True
    assert await wl.add_or_touch(CONTRACT_B, "base", 20.0) is True
    # full now (2/2) -- a better score evicts the worst (A, score 10)
    assert await wl.add_or_touch(CONTRACT_C, "base", 30.0) is True
    assert await wl.count() == 2
    contracts = {r["contract"] for r in await wl.list_all()}
    assert contracts == {CONTRACT_B, CONTRACT_C}


@pytest.mark.asyncio
async def test_watchlist_full_rejects_worse_score_no_partial_write(monkeypatch):
    monkeypatch.setattr(wl, "MAX_WATCHLIST_SIZE", 2)
    await wl.add_or_touch(CONTRACT_A, "base", 50.0)
    await wl.add_or_touch(CONTRACT_B, "base", 60.0)
    added = await wl.add_or_touch(CONTRACT_C, "base", 5.0)  # worse than both
    assert added is False
    assert await wl.count() == 2
    contracts = {r["contract"] for r in await wl.list_all()}
    assert contracts == {CONTRACT_A, CONTRACT_B}


@pytest.mark.asyncio
async def test_add_or_touch_rejects_empty_contract_or_chain():
    assert await wl.add_or_touch("", "base", 10.0) is False
    assert await wl.add_or_touch(CONTRACT_A, "", 10.0) is False
    assert await wl.count() == 0


# ── get_fresh / record_result ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_fresh_returns_none_when_never_checked():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    assert await wl.get_fresh(CONTRACT_A, "base") is None


@pytest.mark.asyncio
async def test_get_fresh_returns_security_after_record_result():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    sec = _security(is_honeypot=False, sell_tax=0.05)
    await wl.record_result(CONTRACT_A, "base", sec)

    fresh = await wl.get_fresh(CONTRACT_A, "base")
    assert fresh is not None
    assert fresh.is_honeypot is False
    assert fresh.sell_tax == 0.05
    assert fresh.available is True


@pytest.mark.asyncio
async def test_get_fresh_none_when_older_than_max_age():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await wl.record_result(CONTRACT_A, "base", _security())
    # a max_age of 0 hours means "checked just now" is already too old
    assert await wl.get_fresh(CONTRACT_A, "base", max_age_hours=0.0) is None


@pytest.mark.asyncio
async def test_record_result_persists_unavailable_result_too():
    """A failed GoPlus call still gets recorded (checked_available=0) -- the
    round-robin must move on rather than retry the same candidate forever."""
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    sec = TokenSecurity(address=CONTRACT_A, available=False, error="rate limit")
    await wl.record_result(CONTRACT_A, "base", sec)
    rows = await wl.list_all()
    assert rows[0]["checked_available"] == 0
    assert rows[0]["last_checked_at"] is not None


# ── next_due (round-robin ordering) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_next_due_prioritizes_never_checked_over_stale():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await wl.record_result(CONTRACT_A, "base", _security())  # now checked
    await wl.add_or_touch(CONTRACT_B, "base", 5.0)  # never checked

    due = await wl.next_due(limit=1)
    assert due[0]["contract"] == CONTRACT_B


@pytest.mark.asyncio
async def test_next_due_empty_watchlist_returns_empty_list():
    assert await wl.next_due(limit=1) == []


# ── remove ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_drops_entry():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await wl.remove(CONTRACT_A, "base")
    assert await wl.count() == 0


# ── format_status_report ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_format_status_report_empty_watchlist():
    report = await wl.format_status_report()
    assert "vide" in report.lower()


@pytest.mark.asyncio
async def test_format_status_report_shows_count_and_chain_breakdown():
    await wl.add_or_touch(CONTRACT_A, "base", 90.0)
    await wl.add_or_touch(CONTRACT_B, "ethereum", 50.0)
    report = await wl.format_status_report()
    assert "2/" in report
    assert "base=1" in report
    assert "ethereum=1" in report


# ── case normalization (Solana base58 case-sensitive, EVM lowercased) ────

@pytest.mark.asyncio
async def test_evm_contract_lowercased_on_write_and_read():
    await wl.add_or_touch(CONTRACT_A.upper(), "base", 10.0)
    assert await wl.get_fresh(CONTRACT_A.lower(), "BASE") is None  # never checked yet, but no crash
    assert await wl.count() == 1


@pytest.mark.asyncio
async def test_solana_contract_case_preserved():
    mixed = "Sol1111111111111111111111111111111111111"
    await wl.add_or_touch(mixed, "solana", 10.0)
    rows = await wl.list_all()
    assert rows[0]["contract"] == mixed


# ---------------------------------------------------------------------------
# 02/09 -- queue reordering (operator P0 #4): live candidates before dead ones
# ---------------------------------------------------------------------------

async def _age_entry(contract: str, chain: str, *, checked_hours_ago: float | None,
                     seen_hours_ago: float | None) -> None:
    """Rewrites the two timestamps directly -- the only way to simulate an
    entry that went stale during the 9 days the cycle was off."""
    import aiosqlite
    from datetime import datetime, timedelta, timezone
    from aria_core.paths import aria_db_path

    now = datetime.now(timezone.utc)
    checked = (now - timedelta(hours=checked_hours_ago)).isoformat() if checked_hours_ago is not None else None
    seen = (now - timedelta(hours=seen_hours_ago)).isoformat() if seen_hours_ago is not None else None
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "UPDATE goplus_watchlist SET last_checked_at = ?, last_seen_at = ? WHERE contract = ? AND chain = ?",
            (checked, seen, contract, chain),
        )
        await db.commit()


async def test_a_live_candidate_is_served_before_an_older_stale_one():
    """The exact production situation on 02/09: a token the radar just saw,
    queued behind August leftovers."""
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)   # vieux, jamais vu recemment
    await wl.add_or_touch(CONTRACT_B, "base", 10.0)   # vu a l'instant par la decouverte
    await _age_entry(CONTRACT_A, "base", checked_hours_ago=400, seen_hours_ago=400)
    await _age_entry(CONTRACT_B, "base", checked_hours_ago=100, seen_hours_ago=0.1)

    due = await wl.next_due(limit=2)
    assert [d["contract"] for d in due] == [CONTRACT_B, CONTRACT_A]


async def test_within_the_live_tier_the_better_score_goes_first():
    await wl.add_or_touch(CONTRACT_A, "base", 5.0)
    await wl.add_or_touch(CONTRACT_B, "base", 90.0)
    for c in (CONTRACT_A, CONTRACT_B):
        await _age_entry(c, "base", checked_hours_ago=100, seen_hours_ago=0.1)

    due = await wl.next_due(limit=2)
    assert [d["contract"] for d in due] == [CONTRACT_B, CONTRACT_A]


async def test_a_still_fresh_verdict_is_never_re_served():
    """Prevents the new ordering from burning budget re-checking a live token
    whose verdict is minutes old -- impossible under the old ordering, real
    risk under this one."""
    await wl.add_or_touch(CONTRACT_A, "base", 50.0)
    await _age_entry(CONTRACT_A, "base", checked_hours_ago=1, seen_hours_ago=0.1)  # frais
    assert await wl.next_due(limit=5) == []

    await _age_entry(CONTRACT_A, "base", checked_hours_ago=wl.WATCHLIST_FRESHNESS_HOURS + 1,
                     seen_hours_ago=0.1)
    assert [d["contract"] for d in await wl.next_due(limit=5)] == [CONTRACT_A]


async def test_the_old_universe_still_refreshes_behind_the_live_tier():
    """The tail must not starve -- with no live candidate, the historical
    round-robin (never-checked first, then oldest) is unchanged."""
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await wl.add_or_touch(CONTRACT_B, "base", 10.0)
    await _age_entry(CONTRACT_A, "base", checked_hours_ago=200, seen_hours_ago=None)
    await _age_entry(CONTRACT_B, "base", checked_hours_ago=None, seen_hours_ago=None)  # jamais verifie

    due = await wl.next_due(limit=2)
    assert [d["contract"] for d in due] == [CONTRACT_B, CONTRACT_A]


async def test_add_or_touch_refreshes_last_seen_on_an_existing_entry():
    await wl.add_or_touch(CONTRACT_A, "base", 10.0)
    await _age_entry(CONTRACT_A, "base", checked_hours_ago=100, seen_hours_ago=500)
    await wl.add_or_touch(CONTRACT_A, "base", 12.0)  # la decouverte le revoit

    due = await wl.next_due(limit=1)
    assert due and due[0]["contract"] == CONTRACT_A
    assert due[0]["last_seen_at"] is not None
