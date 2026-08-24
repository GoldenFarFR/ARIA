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


# --- dedicated polling endpoint ----------------------------------------------
# Batched reads and streaming are billed on different models (Helius: 1 credit
# per call vs 20 credits per MB), so each workload should sit where it costs
# least rather than forcing one provider to be good at both.


def test_the_polling_endpoint_is_used_when_configured(monkeypatch):
    monkeypatch.setenv(tracker.POLLING_RPC_HTTP_ENV, "https://polling.example")
    assert PumpFunCurveTracker()._rpc_http_url == "https://polling.example"


def test_it_falls_back_to_the_main_endpoint_when_unset(monkeypatch):
    monkeypatch.delenv(tracker.POLLING_RPC_HTTP_ENV, raising=False)
    monkeypatch.setattr(tracker, "RPC_HTTP_DEFAULT", "https://main.example")
    assert PumpFunCurveTracker()._rpc_http_url == "https://main.example"


def test_an_explicit_url_still_wins(monkeypatch):
    monkeypatch.setenv(tracker.POLLING_RPC_HTTP_ENV, "https://polling.example")
    assert PumpFunCurveTracker(rpc_http_url="https://explicit.example")._rpc_http_url == \
        "https://explicit.example"


def test_a_blank_env_value_does_not_shadow_the_fallback(monkeypatch):
    # An empty variable is a very common .env accident; it must read as "unset",
    # never as "use the empty string", which would send every poll nowhere.
    monkeypatch.setenv(tracker.POLLING_RPC_HTTP_ENV, "   ")
    monkeypatch.setattr(tracker, "RPC_HTTP_DEFAULT", "https://main.example")
    assert PumpFunCurveTracker()._rpc_http_url == "https://main.example"


# --- rate limiting (regression 2026.08.21) ----------------------------------
# The module shipped with NO throttle, against the project norm that every
# external client paces itself to ~90% of the provider's real sustained rate.
# With 588 mints tracked the sweep fired its batches back to back, drew HTTP
# 429s, and detections fell from 34 to 18 per hour.


def test_batches_are_paced_apart(monkeypatch):
    calls = []

    async def timed(http_client, url, pubkeys):
        calls.append(asyncio.get_event_loop().time())
        return [None] * len(pubkeys)

    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", timed)
    n = MAX_ACCOUNTS_PER_CALL * 3
    t = PumpFunCurveTracker(rpc_http_url="http://rpc.test", max_tracked=n)
    for i in range(n):
        t.add(f"mint{i}", f"pool{i}")

    asyncio.run(t.poll_due(http_client=None, now=10_000.0))

    assert len(calls) == 3
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    # Allow scheduler slack, but the pacing must be real, not incidental.
    assert all(g >= tracker._MIN_INTERVAL_SECONDS * 0.7 for g in gaps), gaps
    assert t.throttled_waits >= 2


def test_the_rate_is_below_the_provider_cap():
    # Chainstack Solana Mainnet Growth plan: 50 req/s specifically (not the
    # general 250 req/s tier, confirmed live 24/08 -- see docs/HANDOFF_CHAINSTACK.md
    # section 3.B; was 5 req/s pre-upgrade on the Developer plan). The norm is
    # ~90% of the real rate, never the ceiling itself.
    assert tracker.MAX_REQUESTS_PER_SECOND < 50.0
    assert tracker.MAX_REQUESTS_PER_SECOND >= 40.0


# --- provider cascade (2026.08.21, RPS figure corrected 24/08) --------------
# Chainstack primary, Helius fallback. Chainstack wins on measured facts: 3M
# units/month vs 1M, and a flat 1 unit per call instead of a per-megabyte
# streaming rate -- despite a slower real Solana Mainnet cap (5 req/s vs
# Helius's 10, corrected 24/08 from an earlier 25 req/s figure that mixed up
# Chainstack's general-plan rate with its Solana-specific one, see
# docs/HANDOFF_CHAINSTACK.md section 3.B). The fallback matters because a failed batch is
# not neutral -- its mints go unmeasured for that round.


def _two_provider_tracker():
    return PumpFunCurveTracker(rpc_http_url="http://primary.test",
                               fallback_http_url="http://fallback.test", max_tracked=10)


def test_the_primary_is_used_when_it_answers(monkeypatch):
    seen = []

    async def ok(http_client, url, pubkeys):
        seen.append(url)
        return [None] * len(pubkeys)

    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", ok)
    t = _two_provider_tracker()
    t.add("m", "p")
    asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert seen == ["http://primary.test"]
    assert t._endpoints[1].calls == 0


def test_it_falls_back_when_the_primary_refuses(monkeypatch):
    seen = []

    async def flaky(http_client, url, pubkeys):
        seen.append(url)
        if url == "http://primary.test":
            raise RuntimeError("429 Too Many Requests")
        return [None] * len(pubkeys)

    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", flaky)
    t = _two_provider_tracker()
    t.add("m", "p")
    asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert seen == ["http://primary.test", "http://fallback.test"]
    assert t._endpoints[0].failures == 1
    assert t._endpoints[1].calls == 1
    assert t.credits_spent == 1  # billed once, on the provider that answered


def test_a_failure_never_disables_a_provider(monkeypatch):
    # A transient error must not sideline the primary: permanently demoting it
    # on one bad response would be worse than retrying next sweep.
    calls = {"n": 0}

    async def once_bad(http_client, url, pubkeys):
        calls["n"] += 1
        if url == "http://primary.test" and calls["n"] == 1:
            raise RuntimeError("boom")
        return [None] * len(pubkeys)

    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", once_bad)
    t = _two_provider_tracker()
    t.add("m", "p")
    asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    t._tracked["m"].last_polled_at = 0.0
    asyncio.run(t.poll_due(http_client=None, now=20_000.0))
    assert t._endpoints[0].calls == 1  # primary tried again and succeeded


def test_each_provider_keeps_its_own_pace():
    # A shared throttle would silently average the two rates together instead
    # of respecting each provider's own real cap. 24/08: Chainstack's real
    # Solana Mainnet cap moved from 5 req/s (Developer plan) to 50 req/s
    # (Growth plan, operator upgraded the same day) -- Chainstack is now
    # FASTER than Helius (10 req/s) here, the reverse of the pre-upgrade
    # ordering. The point of this test is that each endpoint's pacing stays
    # independently correct either way, not which provider happens to be
    # faster.
    t = _two_provider_tracker()
    assert t._endpoints[0].max_rps == tracker.CHAINSTACK_MAX_RPS
    assert t._endpoints[1].max_rps == tracker.HELIUS_MAX_RPS
    assert t._endpoints[0].min_interval < t._endpoints[1].min_interval
    assert tracker.HELIUS_MAX_RPS < 10.0      # under the published Helius cap
    assert tracker.CHAINSTACK_MAX_RPS < 50.0  # under the real Solana Mainnet cap (50 req/s, Growth)


def test_a_single_provider_still_works_alone(monkeypatch):
    async def ok(http_client, url, pubkeys):
        return [None] * len(pubkeys)
    monkeypatch.setattr(tracker, "_rpc_get_multiple_accounts", ok)
    t = PumpFunCurveTracker(rpc_http_url="http://only.test", fallback_http_url="")
    assert len(t._endpoints) == 1
    t.add("m", "p")
    asyncio.run(t.poll_due(http_client=None, now=10_000.0))
    assert t._endpoints[0].calls == 1


class TestTimeToQualify:
    """22/08 -- the strongest signal measured on the late-bonding pocket:
    closures that qualified in under 60s returned +29.06% after the outlier
    test, against +10.53% for those taking over 180s (282 archived paths).

    Recorded here rather than derived from the trade stream, whose own
    divide-by-duration metric (`sol_velocity`) inverted sign depending on how
    long we had been watching -- the duration WAS the signal and the ratio
    threw it away. Every candidate passes through this tracker."""

    def test_a_tracked_mint_reports_how_long_it_has_been_followed(self):
        from aria_core.services.pumpfun_curve_tracker import PumpFunCurveTracker

        tracker = PumpFunCurveTracker()
        tracker.add("mint1", "pool1")
        entry = tracker._tracked["mint1"]

        assert tracker.seconds_tracked("mint1", now=entry.first_seen_at + 45.0) == 45.0

    def test_an_unknown_mint_is_unknown_never_instant(self):
        """0.0 would read as "qualified instantly", the most flattering
        possible value for the strongest signal we have."""
        from aria_core.services.pumpfun_curve_tracker import PumpFunCurveTracker

        assert PumpFunCurveTracker().seconds_tracked("never-seen") is None

    def test_re_adding_a_tracked_mint_does_not_reset_its_age(self):
        """`add` is called repeatedly from the creation feed. Resetting on each
        call would make every long-running token look brand new."""
        from aria_core.services.pumpfun_curve_tracker import PumpFunCurveTracker

        tracker = PumpFunCurveTracker()
        tracker.add("mint1", "pool1")
        first = tracker._tracked["mint1"].first_seen_at

        tracker.add("mint1", "pool1")

        assert tracker._tracked["mint1"].first_seen_at == first

    def test_the_age_survives_a_restart(self, tmp_path):
        """Without this, every restart resets every mint to "just seen" --
        flattering precisely the signal being measured."""
        import time

        from aria_core.services.pumpfun_curve_tracker import PumpFunCurveTracker

        path = str(tmp_path / "state.json")
        saved = PumpFunCurveTracker()
        saved.add("mint1", "pool1")
        saved._tracked["mint1"].first_seen_at = time.monotonic() - 300.0
        saved._tracked["mint1"].progress = 0.5
        saved.save_state(path)

        restored = PumpFunCurveTracker()
        assert restored.load_state(path) == 1

        age = restored.seconds_tracked("mint1")
        assert age is not None and age >= 300.0, (
            f"a mint tracked for 300s must not come back younger (got {age})"
        )
