"""Item #247 (30/07) -- log of RSI divergence "steepness" (gap/span reduced
to an angle in degrees) vs outcome (bought/rejected). DB isolated per test,
no real network call."""
from __future__ import annotations

import math

import pytest

from aria_core import rsi_divergence_log as rdl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rdl, "DB_PATH", str(tmp_path / "rsi_divergence_test.db"))


def test_compute_angle_deg_steep_vs_shallow():
    """A bigger RSI gap over the same span reads as a STEEPER angle (closer
    to 90°); a smaller gap over the same span reads as shallower -- the
    whole point of using atan2 rather than a plain ratio."""
    steep = rdl.compute_angle_deg(gap=20.0, span=5)
    shallow = rdl.compute_angle_deg(gap=2.0, span=5)
    assert steep > shallow
    assert 0.0 < shallow < steep < 90.0


def test_compute_angle_deg_matches_hand_computed_value():
    # atan2(10, 10) = 45 degrees exactly -- a clean sanity check.
    assert rdl.compute_angle_deg(gap=10.0, span=10) == pytest.approx(45.0)


def test_compute_angle_deg_none_when_gap_missing():
    assert rdl.compute_angle_deg(gap=None, span=10) is None


def test_compute_angle_deg_none_when_span_missing():
    assert rdl.compute_angle_deg(gap=5.0, span=None) is None


def test_compute_angle_deg_none_when_span_non_positive():
    """A zero/negative span is a degenerate input (no real candle distance
    to measure an incline over) -- never a fabricated angle, never a
    division-by-zero crash."""
    assert rdl.compute_angle_deg(gap=5.0, span=0) is None
    assert rdl.compute_angle_deg(gap=5.0, span=-3) is None


@pytest.mark.asyncio
async def test_record_divergence_persists_computed_angle():
    await rdl.record_divergence(
        "0xAAA", "base", symbol="TOK", wallet="scalping", mode="scalping",
        gap=10.0, span=10, outcome="bought_direct",
    )
    rows = await rdl.recent_entries()
    assert len(rows) == 1
    assert rows[0]["angle_deg"] == pytest.approx(45.0)
    assert rows[0]["contract"] == "0xaaa"
    assert rows[0]["outcome"] == "bought_direct"


@pytest.mark.asyncio
async def test_record_divergence_persists_last_seen_span_for_unconfirmed_outcomes():
    """04/08, operator request ("ajuste les log pour qu'il récupère plus
    d'informations"): expired/cancelled watches never had a CONFIRMED
    divergence (gap/span stay None, unchanged), but the last candidate
    OBSERVED along the way (even outside the trigger window) is now
    captured separately -- so these rows aren't a flat, unanalyzable
    "never"."""
    await rdl.record_divergence(
        "0xAAA", "base", outcome="expired_unconfirmed",
        last_seen_span=6, last_seen_gap=3.0,
    )
    rows = await rdl.recent_entries()
    assert rows[0]["gap"] is None  # never fabricated -- no CONFIRMED divergence
    assert rows[0]["span"] is None
    assert rows[0]["last_seen_span"] == 6
    assert rows[0]["last_seen_gap"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_record_divergence_no_angle_for_unconfirmed_outcomes():
    """An expired/cancelled watch never had a confirmed divergence -- no
    gap/span exist to measure, so no angle is fabricated."""
    await rdl.record_divergence(
        "0xAAA", "base", outcome="expired_unconfirmed",
    )
    rows = await rdl.recent_entries()
    assert rows[0]["angle_deg"] is None
    assert rows[0]["gap"] is None
    assert rows[0]["span"] is None


@pytest.mark.asyncio
async def test_record_divergence_rejects_unknown_outcome():
    """A typo'd outcome would silently create an unanalyzable bucket --
    defensive no-op instead."""
    await rdl.record_divergence("0xAAA", "base", outcome="not_a_real_outcome")
    assert await rdl.recent_entries() == []


@pytest.mark.asyncio
async def test_record_divergence_missing_contract_is_noop():
    await rdl.record_divergence("", "base", outcome="bought_direct")
    assert await rdl.recent_entries() == []


@pytest.mark.asyncio
async def test_recent_entries_newest_first_and_capped():
    for i in range(5):
        await rdl.record_divergence(f"0x{i}", "base", outcome="bought_direct", gap=1.0, span=1)
    rows = await rdl.recent_entries(limit=3)
    assert len(rows) == 3
    assert rows[0]["contract"] == "0x4"  # most recently inserted


@pytest.mark.asyncio
async def test_summarize_by_outcome_counts_and_averages():
    await rdl.record_divergence("0xA", "base", outcome="bought_direct", gap=10.0, span=10)  # 45deg
    await rdl.record_divergence("0xB", "base", outcome="bought_direct", gap=10.0, span=20)  # ~26.6deg
    await rdl.record_divergence("0xC", "base", outcome="expired_unconfirmed")
    await rdl.record_divergence("0xD", "base", outcome="expired_unconfirmed")
    await rdl.record_divergence("0xE", "base", outcome="expired_unconfirmed")

    summary = await rdl.summarize_by_outcome()

    assert summary["bought_direct"]["count"] == 2
    assert summary["bought_direct"]["avg_angle_deg"] == pytest.approx((45.0 + 26.565051177078) / 2)
    # Item #250 (30/07), operator request ("aussi la longueur de la
    # divergence") -- span (candle length) tracked alongside the angle,
    # never conflated with it (two divergences can share an angle while
    # spanning very different real durations).
    assert summary["bought_direct"]["avg_span"] == pytest.approx(15.0)
    assert summary["bought_direct"]["min_span"] == 10
    assert summary["bought_direct"]["max_span"] == 20
    assert summary["expired_unconfirmed"]["count"] == 3
    assert summary["expired_unconfirmed"]["avg_angle_deg"] is None  # never a fabricated average
    assert summary["expired_unconfirmed"]["avg_span"] is None  # never a fabricated average
    assert summary["cancelled_unconfirmed"]["count"] == 0
    assert summary["bought_via_limit_order"]["count"] == 0


@pytest.mark.asyncio
async def test_summarize_by_outcome_averages_last_seen_span_separately():
    """04/08: last_seen_span/gap are a SEPARATE aggregate from the confirmed
    span/gap -- an expired bucket with zero confirmed divergences can still
    report how close its watches got."""
    await rdl.record_divergence(
        "0xA", "base", outcome="expired_unconfirmed", last_seen_span=6, last_seen_gap=3.0,
    )
    await rdl.record_divergence(
        "0xB", "base", outcome="expired_unconfirmed", last_seen_span=10, last_seen_gap=5.0,
    )
    await rdl.record_divergence("0xC", "base", outcome="expired_unconfirmed")  # never saw anything

    summary = await rdl.summarize_by_outcome()

    assert summary["expired_unconfirmed"]["avg_span"] is None  # still never fabricated
    assert summary["expired_unconfirmed"]["avg_last_seen_span"] == pytest.approx(8.0)
    assert summary["expired_unconfirmed"]["min_last_seen_span"] == 6
    assert summary["expired_unconfirmed"]["max_last_seen_span"] == 10


@pytest.mark.asyncio
async def test_summarize_by_outcome_empty_log():
    summary = await rdl.summarize_by_outcome()
    for outcome in rdl.OUTCOMES:
        assert summary[outcome]["count"] == 0
        assert summary[outcome]["avg_angle_deg"] is None
        assert summary[outcome]["avg_span"] is None


def test_format_summary_report_degrades_honestly_on_empty_buckets():
    summary = {
        "bought_direct": {
            "count": 0, "avg_angle_deg": None, "min_angle_deg": None, "max_angle_deg": None,
            "avg_span": None, "min_span": None, "max_span": None,
            "avg_last_seen_span": None, "min_last_seen_span": None, "max_last_seen_span": None,
        },
        "bought_via_limit_order": {
            "count": 2, "avg_angle_deg": 30.5, "min_angle_deg": 20.0, "max_angle_deg": 41.0,
            "avg_span": 17.0, "min_span": 15, "max_span": 19,
            "avg_last_seen_span": None, "min_last_seen_span": None, "max_last_seen_span": None,
        },
        "expired_unconfirmed": {
            "count": 4, "avg_angle_deg": None, "min_angle_deg": None, "max_angle_deg": None,
            "avg_span": None, "min_span": None, "max_span": None,
            "avg_last_seen_span": 8.0, "min_last_seen_span": 6, "max_last_seen_span": 10,
        },
        "cancelled_unconfirmed": {
            "count": 0, "avg_angle_deg": None, "min_angle_deg": None, "max_angle_deg": None,
            "avg_span": None, "min_span": None, "max_span": None,
            "avg_last_seen_span": None, "min_last_seen_span": None, "max_last_seen_span": None,
        },
    }
    report = rdl.format_summary_report(summary)
    assert "aucune entrée" in report  # bought_direct, cancelled_unconfirmed
    assert "angle moyen 30.5°" in report
    assert "longueur moyenne 17.0 bougies" in report
    assert "4 (angle non mesurable)" in report  # expired_unconfirmed
    # 04/08 -- last-seen (non confirmed) span surfaces even without a confirmed angle
    assert "dernier span observé (non confirmé) : moyenne 8.0" in report
