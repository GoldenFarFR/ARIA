"""A frozen hypothesis must be judged, never quietly adjusted.

The module exists because five entry filters were proposed in one session and
four evaporated on inspection -- all of them found by searching an already
observed sample. These tests protect the one property that makes the mechanism
worth anything: the claim is written down BEFORE the data arrives, and nothing
in the code path may soften it afterwards."""
from __future__ import annotations

import sqlite3

import pytest

from aria_core import frozen_entry_hypothesis as fz


def _make_db(tmp_path, rows: list[dict]) -> str:
    path = str(tmp_path / "shadow.db")
    con = sqlite3.connect(path)
    con.execute(
        f"""CREATE TABLE {fz.TABLE} (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               token_address TEXT, detected_at TEXT, exit_reason TEXT,
               final_multiplier REAL, reserve_usd REAL,
               distinct_buyers_at_entry INTEGER, top_buyer_share_at_entry REAL)"""
    )
    con.executemany(
        f"INSERT INTO {fz.TABLE} (token_address, detected_at, exit_reason, "
        f" final_multiplier, reserve_usd, distinct_buyers_at_entry, top_buyer_share_at_entry) "
        f"VALUES (:token, :at, :exit, :mult, :reserve, :buyers, :share)",
        rows,
    )
    con.commit()
    con.close()
    return path


def _row(i: int, mult: float, *, reserve=7000.0, share=0.06, buyers=100, token=None) -> dict:
    return {
        "token": token or f"tok{i}", "at": f"2026-08-22T{10 + i // 60:02d}:{i % 60:02d}:00",
        "exit": "trailing_stop", "mult": mult, "reserve": reserve,
        "buyers": buyers, "share": share,
    }


def test_a_hypothesis_that_holds_is_reported_as_holding(tmp_path):
    # kept side (share/reserve above A's thresholds) clearly outperforms
    rows = [_row(i, 2.0) for i in range(80)]
    rows += [_row(100 + i, 1.0, reserve=6000.0, share=0.01) for i in range(80)]

    report = fz.build_report(db_path=_make_db(tmp_path, rows))
    result = next(r for r in report["results"] if r["hypothesis"] == "A")

    assert result["verdict"] == "holds"
    assert result["observed_edge_pct"] > 0


def test_a_hypothesis_that_reverses_is_reported_as_broken(tmp_path):
    """The case that matters: the kept side does WORSE than the rejected one."""
    rows = [_row(i, 0.5) for i in range(80)]
    rows += [_row(100 + i, 2.0, reserve=6000.0, share=0.01) for i in range(80)]

    report = fz.build_report(db_path=_make_db(tmp_path, rows))
    result = next(r for r in report["results"] if r["hypothesis"] == "A")

    assert result["verdict"] == "broken"
    assert result["observed_edge_pct"] < 0


def test_a_small_sample_never_produces_a_verdict(tmp_path):
    """46 live closures were enough to see A's sign go negative and NOT enough
    to conclude. Reading a small sample as a result is the exact failure this
    module was built against."""
    rows = [_row(i, 2.0) for i in range(20)]
    rows += [_row(100 + i, 1.0, reserve=6000.0, share=0.01) for i in range(20)]

    report = fz.build_report(db_path=_make_db(tmp_path, rows))

    assert all(r["verdict"] == "insufficient" for r in report["results"])


def test_re_entries_are_excluded_from_every_hypothesis(tmp_path):
    """The cooldown is already live, so measuring against re-entries would
    credit a filter for a rule the pocket already applies."""
    rows = [_row(i, 2.0, token="same") for i in range(10)]

    loaded = fz.load_closures(db_path=_make_db(tmp_path, rows))

    assert [r["_rank"] for r in loaded] == list(range(1, 11))
    assert sum(1 for r in loaded if fz.CANDIDATE_A.predicate(r)) == 1


def test_the_frozen_numbers_are_stated_and_not_derived(tmp_path):
    """If the expected values could be recomputed from live data the mechanism
    would be circular. They are constants, and they carry their caveats."""
    assert fz.CANDIDATE_A.expected_avg_pct == 41.19
    assert fz.CANDIDATE_A.expected_without_top5_pct == 28.61
    assert fz.CANDIDATE_A.expected_edge_pct == pytest.approx(10.0)
    assert fz.CANDIDATE_A.found_on_closures == 670
    assert any("permutation" in c for c in fz.CANDIDATE_A.caveats), (
        "a candidate that failed the multiple-testing correction must say so"
    )


def test_scoring_removes_the_top_five(tmp_path):
    """In this dome ~1.8% of trades carry the whole gain, so a raw average
    describes which outliers landed in the sample, not the strategy."""
    rows = [_row(i, 1.0) for i in range(20)] + [_row(100 + i, 11.0) for i in range(5)]
    loaded = fz.load_closures(db_path=_make_db(tmp_path, rows))

    scored = fz.score(loaded)

    assert scored["avg_pct"] > 100
    assert scored["without_top5_pct"] == pytest.approx(0.0, abs=0.01)


def test_the_module_never_writes(tmp_path):
    """It judges. Changing what the pocket buys stays the operator's call."""
    db = _make_db(tmp_path, [_row(i, 2.0) for i in range(80)])
    fz.build_report(db_path=db)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert con.execute(f"SELECT COUNT(*) FROM {fz.TABLE}").fetchone()[0] == 80
    finally:
        con.close()
