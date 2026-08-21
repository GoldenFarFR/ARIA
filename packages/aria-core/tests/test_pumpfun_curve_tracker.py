"""Batched-polling curve tracker.

Measured 2026.08.21: streaming the whole pump.fun program costs ~98 000 Helius
credits/day (20 credits/MB over 4.9 GB), while getMultipleAccounts costs 1
credit per CALL for up to 100 accounts. These cover the polling shape that
replaces the stream, and above all the two things that would silently make it
expensive again: unchunked batches and an unbounded tracked set.
"""

from __future__ import annotations

import asyncio

import pytest

from aria_core.services import pumpfun_curve_tracker as tracker
from aria_core.services.pumpfun_curve_tracker import (
    HANDOVER_PROGRESS,
    INITIAL_CURVE_TOKENS,
    MAX_ACCOUNTS_PER_CALL,
    PumpFunCurveTracker,
    band_for,
    decode_curve_progress,
)


def _curve_bytes(progress: float, *, decimals: int = 6, complete: bool = False) -> bytes:
    """A synthetic BondingCurve account sitting at `progress`."""
    total = INITIAL_CURVE_TOKENS * (10 ** decimals)
    left = int(round(total * (1.0 - progress)))
    raw = bytearray(64)
    raw[tracker.OFF_REAL_TOKEN_RESERVES:tracker.OFF_REAL_TOKEN_RESERVES + 8] = left.to_bytes(8, "little")
    raw[tracker.OFF_COMPLETE] = 1 if complete else 0
    return bytes(raw)


def test_progress_is_decoded_from_the_reserves():
    assert decode_curve_progress(_curve_bytes(0.50), 6) == pytest.approx(0.50, abs=1e-9)
    assert decode_curve_progress(_curve_bytes(0.0), 6) == pytest.approx(0.0, abs=1e-9)


def test_a_completed_curve_reads_as_graduated():
    assert decode_curve_progress(_curve_bytes(0.9, complete=True), 6) == 1.0


def test_a_truncated_account_reads_none_never_zero():
    # Zero would mean "brand new token", which is a completely different fact
    # from "this is not the account we think it is".
    assert decode_curve_progress(b"\x00" * 10, 6) is None


def test_reserves_above_the_initial_allocation_read_none():
    raw = bytearray(_curve_bytes(0.5))
    huge = (INITIAL_CURVE_TOKENS * (10 ** 6) * 2)
    raw[tracker.OFF_REAL_TOKEN_RESERVES:tracker.OFF_REAL_TOKEN_RESERVES + 8] = huge.to_bytes(8, "little")
    assert decode_curve_progress(bytes(raw), 6) is None


def test_bands_get_faster_as_the_curve_fills():
    slow = band_for(0.10)[2]
    mid = band_for(0.40)[2]
    fast = band_for(0.60)[2]
    assert slow > mid > fast


def test_past_handover_the_tracker_steps_aside():
    # The pocket's own targeted subscription owns it from there; polling on
    # top would pay twice for the same token.
    assert band_for(HANDOVER_PROGRESS) is None
    assert band_for(0.95) is None


def test_an_unknown_progress_polls_at_the_slowest_cadence():
    assert band_for(None) == tracker.BAND_EDGES[0]


def test_a_mint_first_seen_above_the_threshold_is_not_a_crossing():
    # We never watched it climb, so there is no history to act on -- treating
    # it as a crossing would trigger an entry on a token we know nothing about.
    assert PumpFunCurveTracker.crossed(None, 0.55, 0.50) is False
    assert PumpFunCurveTracker.crossed(0.48, 0.55, 0.50) is True
    assert PumpFunCurveTracker.crossed(0.52, 0.58, 0.50) is False


def test_the_tracked_set_is_capped_and_refusals_counted():
    t = PumpFunCurveTracker(max_tracked=2)
    assert t.add("a", "poolA") is True
    assert t.add("b", "poolB") is True
    assert t.add("c", "poolC") is False
    assert t.tracked_count() == 2
    assert t.refused_adds == 1


def test_adding_the_same_mint_twice_costs_one_slot():
    t = PumpFunCurveTracker(max_tracked=2)
    t.add("a", "poolA")
    t.add("a", "poolA")
    assert t.tracked_count() == 1


def test_a_frozen_mint_is_pruned_so_the_poll_stays_cheap():
    t = PumpFunCurveTracker()
    t.add("a", "poolA")
    t._tracked["a"].last_change_at = 0.0
    assert t.prune(now=tracker.STALE_AFTER_SECONDS + 1.0) == 1
    assert t.tracked_count() == 0


def test_only_mints_past_their_band_cadence_are_due():
    t = PumpFunCurveTracker()
    t.add("a", "poolA")
    t._tracked["a"].progress = 0.60          # fast band
    t._tracked["a"].last_polled_at = 100.0
    fast_interval = band_for(0.60)[2]
    assert t.due(now=100.0 + fast_interval - 0.1) == []
    assert [e.mint for e in t.due(now=100.0 + fast_interval + 0.1)] == ["a"]


class _FakeRpc:
    """Records every call so the test can assert on batch SIZE, which is what
    decides both correctness (Solana caps at 100) and cost (1 credit/call)."""

    def __init__(self, progress_by_pool):
        self.calls: list[list[str]] = []
        self._progress = progress_by_pool

    async def __call__(self, http_client, url, pubkeys):
        self.calls.append(list(pubkeys))
        import base64
        out = []
        for pk in pubkeys:
            p = self._progress.get(pk)
            if p is None:
                out.append(None)
                continue
            out.append({"data": [base64.b64encode(_curve_bytes(p)).decode(), "base64"]})
        return out


def test_a_large_set_is_chunked_to_the_solana_limit(monkeypatch):
    n = MAX_ACCOUNTS_PER_CALL * 2 + 5
    progress = {f"pool{i}": 0.10 for i in range(n)}
    fake = _FakeRpc(progress)
    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", fake)
    t = PumpFunCurveTracker(rpc_http_url="http://rpc.test", max_tracked=n)
    for i in range(n):
        t.add(f"mint{i}", f"pool{i}")

    asyncio.run(t.poll_due(http_client=None, now=10_000.0))

    assert len(fake.calls) == 3
    assert all(len(c) <= MAX_ACCOUNTS_PER_CALL for c in fake.calls)
    # 1 credit per CALL, never per account -- the whole reason polling beats
    # streaming here.
    assert t.credits_spent == 3


def test_a_poll_reports_only_mints_that_actually_moved(monkeypatch):
    fake = _FakeRpc({"poolA": 0.55})
    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", fake)
    t = PumpFunCurveTracker(rpc_http_url="http://rpc.test")
    t.add("mintA", "poolA")

    first = asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert [(m, prev, round(new, 4)) for m, prev, new in first] == [("mintA", None, 0.55)]

    # Same reserves on the next pass: nothing moved, nothing reported.
    second = asyncio.run(t.poll_due(http_client=None, now=20_000.0))
    assert second == []


def test_a_failing_batch_never_stops_the_others(monkeypatch):
    calls = {"n": 0}

    async def flaky(http_client, url, pubkeys):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("RPC error: boom")
        import base64
        return [{"data": [base64.b64encode(_curve_bytes(0.42)).decode(), "base64"]}
                for _ in pubkeys]

    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", flaky)
    n = MAX_ACCOUNTS_PER_CALL + 1
    t = PumpFunCurveTracker(rpc_http_url="http://rpc.test", max_tracked=n)
    for i in range(n):
        t.add(f"mint{i}", f"pool{i}")

    moved = asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert calls["n"] == 2
    assert moved  # the second batch still landed
    # The failed call is not billed as a success.
    assert t.credits_spent == 1


def test_missing_accounts_are_skipped_not_guessed(monkeypatch):
    fake = _FakeRpc({"poolA": 0.55})  # poolB resolves to None
    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", fake)
    t = PumpFunCurveTracker(rpc_http_url="http://rpc.test")
    t.add("mintA", "poolA")
    t.add("mintB", "poolB")
    moved = asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert [m for m, _, _ in moved] == ["mintA"]
    assert t.progress_of("mintB") is None


# --- state persistence -------------------------------------------------------
# Real incident 2026.08.21: a restart emptied the tracker and a switchover done
# in the following minute found it with nothing to offer. It only knows tokens
# created after it connects, and they need minutes to climb.


def test_a_restart_no_longer_starts_blind(tmp_path):
    path = str(tmp_path / "curve_state.json")
    a = PumpFunCurveTracker()
    a.add("mintA", "poolA")
    a._tracked["mintA"].progress = 0.42
    assert a.save_state(path) == 1

    b = PumpFunCurveTracker()
    assert b.load_state(path) == 1
    assert b.tracked_count() == 1
    assert b.progress_of("mintA") == 0.42


def test_mints_that_went_stale_while_down_are_not_reloaded(tmp_path):
    path = str(tmp_path / "curve_state.json")
    a = PumpFunCurveTracker()
    a.add("mintA", "poolA")
    a.save_state(path)

    # Rewrite the saved timestamp as if the process had been down far longer
    # than the staleness window: polling those would spend credits on corpses.
    import json
    rows = json.load(open(path))
    rows[0]["saved_at"] = rows[0]["saved_at"] - (tracker.STALE_AFTER_SECONDS + 60)
    json.dump(rows, open(path, "w"))

    b = PumpFunCurveTracker()
    assert b.load_state(path) == 0
    assert b.tracked_count() == 0


def test_a_missing_state_file_is_not_an_error(tmp_path):
    t = PumpFunCurveTracker()
    assert t.load_state(str(tmp_path / "nope.json")) == 0


def test_a_corrupt_state_file_starts_empty_rather_than_crashing(tmp_path):
    path = tmp_path / "curve_state.json"
    path.write_text("{ this is not json")
    t = PumpFunCurveTracker()
    assert t.load_state(str(path)) == 0


def test_loading_respects_the_cap(tmp_path):
    path = str(tmp_path / "curve_state.json")
    a = PumpFunCurveTracker(max_tracked=5)
    for i in range(5):
        a.add(f"mint{i}", f"pool{i}")
    a.save_state(path)

    b = PumpFunCurveTracker(max_tracked=2)
    assert b.load_state(path) == 2
    assert b.refused_adds == 3


def test_the_state_file_is_swapped_atomically(tmp_path):
    # A half-written state file would be worse than none: the next start would
    # silently restore a truncated set and read as "the market is quiet".
    path = tmp_path / "curve_state.json"
    t = PumpFunCurveTracker()
    t.add("mintA", "poolA")
    t.save_state(str(path))
    assert path.exists()
    assert not (tmp_path / "curve_state.json.tmp").exists()
