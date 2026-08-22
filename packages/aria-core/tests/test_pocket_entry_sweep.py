"""The sweep must find a real signal AND refuse a fitted one.

Both halves matter equally: a sweep that only finds things would have endorsed
the three filters this module was written to catch (a +25% profit floor, a
tighter stop, a low-activity entry segment) -- each looked strong on a partial
sample and evaporated on the full one."""
from __future__ import annotations

import sqlite3

import pytest

from aria_core import pocket_entry_sweep as sweep


def _make_db(tmp_path, rows: list[dict]) -> str:
    """A minimal stand-in for a pocket's log table."""
    path = str(tmp_path / "shadow.db")
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE solana_late_bonding_shadow_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               detected_at TEXT, exit_reason TEXT, final_multiplier REAL,
               reserve_usd REAL, distinct_buyers_at_entry INTEGER,
               entry_price REAL, peak_price REAL, last_price REAL)"""
    )
    con.executemany(
        "INSERT INTO solana_late_bonding_shadow_log "
        "(detected_at, exit_reason, final_multiplier, reserve_usd, distinct_buyers_at_entry, "
        " entry_price, peak_price, last_price) "
        "VALUES (:detected_at, :exit_reason, :final_multiplier, :reserve_usd, :buyers, "
        "        :entry_price, :peak_price, :last_price)",
        rows,
    )
    con.commit()
    con.close()
    return path


def _row(day: str, mult: float, reserve: float, buyers: int = 50,
         *, peak: float | None = None, entry: float | None = None,
         last: float | None = None) -> dict:
    """`peak`/`entry`/`last` default to a coherent row; set them to reproduce a
    corrupted one."""
    entry = 0.001 if entry is None else entry
    return {
        "detected_at": f"2026-08-{day}T12:00:00+00:00",
        "exit_reason": "trailing_stop",
        "final_multiplier": mult,
        "reserve_usd": reserve,
        "buyers": buyers,
        "entry_price": entry,
        "peak_price": entry * max(mult, 1.0) if peak is None else peak,
        "last_price": entry * mult if last is None else last,
    }


def _real_signal_rows() -> list[dict]:
    """Thin pools lose, deep pools win -- on both days, across every band."""
    rows = []
    for day in ("20", "21"):
        for i in range(150):
            rows.append(_row(day, 0.70 + i * 0.0005, 1000.0 + i * 10))   # thin: losers
            rows.append(_row(day, 1.25 + i * 0.0010, 6000.0 + i * 10))   # deep: winners
    return rows


def test_finds_a_signal_that_is_really_there(tmp_path):
    db = _make_db(tmp_path, _real_signal_rows())
    report = sweep.build_report("late_bonding", db_path=db)

    assert report["verdict"] == "candidate_found"
    best = report["survivors"][0]
    assert best["metric"] == "reserve_usd"
    assert best["sense"] == "min"
    assert best["kept"]["avg_pct"] > report["baseline"]["avg_pct"]


def test_rejects_a_filter_that_only_two_outliers_hold_up(tmp_path):
    """The exact failure mode this module exists for: an average carried by a
    couple of trades. Remove them and the edge is gone -- so the sweep must not
    report it as a survivor."""
    rows = []
    for day in ("20", "21"):
        for i in range(150):
            rows.append(_row(day, 1.00, 1000.0 + i * 10))
            rows.append(_row(day, 1.00, 6000.0 + i * 10))
    # two monster trades on the deep side, and nothing else separating them
    rows.append(_row("21", 40.0, 9000.0))
    rows.append(_row("21", 40.0, 9500.0))

    db = _make_db(tmp_path, rows)
    report = sweep.build_report("late_bonding", db_path=db)

    assert report["verdict"] == "no_filter_survives"
    assert all(not c["checks"]["outlier"] for c in report["best_rejected"])


def test_rejects_a_filter_that_works_on_one_day_only(tmp_path):
    """A filter fitted to a single day describes that day, not the market."""
    rows = []
    for i in range(150):                                   # day 20: signal holds
        rows.append(_row("20", 0.70, 1000.0 + i * 10))
        rows.append(_row("20", 1.60, 6000.0 + i * 10))
    for i in range(150):                                   # day 21: it inverts
        rows.append(_row("21", 1.60, 1000.0 + i * 10))
        rows.append(_row("21", 0.70, 6000.0 + i * 10))

    db = _make_db(tmp_path, rows)
    report = sweep.build_report("late_bonding", db_path=db)

    reserve_survivors = [c for c in report["survivors"] if c["metric"] == "reserve_usd"]
    assert not reserve_survivors, "a filter that inverts between days must not survive"


def test_a_small_sample_never_yields_a_verdict(tmp_path):
    db = _make_db(tmp_path, [_row("21", 1.5, 6000.0) for _ in range(40)])
    report = sweep.build_report("late_bonding", db_path=db)

    assert report["verdict"] == "insufficient"
    assert all(not c["checks"]["sample"] for c in report["best_rejected"])


def test_outcome_columns_are_never_offered_as_filters(tmp_path):
    """Filtering on how a trade ENDED is look-ahead. Only columns describing
    the world before the buy may be swept."""
    db = _make_db(tmp_path, _real_signal_rows())
    rows = sweep.load_closures("late_bonding", db_path=db)
    metrics = sweep.entry_metrics(rows)

    assert "reserve_usd" in metrics
    for outcome in ("final_multiplier", "peak_price", "last_price", "id"):
        assert outcome not in metrics


def test_an_unreadable_metric_never_rejects_a_candidate(tmp_path):
    """Fail-open, same discipline as the pockets themselves: a missing value is
    a reason not to filter, never a reason to skip a trade."""
    rows = sweep.load_closures(
        "late_bonding", db_path=_make_db(tmp_path, _real_signal_rows())
    )
    rows.append({"detected_at": "2026-08-21T12:00:00+00:00", "exit_reason": "trailing_stop",
                 "final_multiplier": 2.0, "reserve_usd": None, "distinct_buyers_at_entry": 1})

    kept, rejected = sweep._split(rows, "reserve_usd", 5500.0, "min")

    assert not any(r["reserve_usd"] is None for r in rejected)
    assert any(r["reserve_usd"] is None for r in kept)


def test_the_sweep_never_writes(tmp_path):
    """It observes. Changing what a pocket buys is a decision, and one that
    touches real capital -- it belongs to the operator, not to a report."""
    db = _make_db(tmp_path, _real_signal_rows())
    sweep.build_report("late_bonding", db_path=db)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        remaining = con.execute(
            "SELECT COUNT(*) FROM solana_late_bonding_shadow_log"
        ).fetchone()[0]
    finally:
        con.close()
    assert remaining == len(_real_signal_rows())


def test_every_column_is_classified():
    """22/08, operator-directed ("pense a alimenter cette outil si tu ajoute des
    nouvelles données recuperable et tout").

    A new pre-trade metric is swept automatically IF its column ends in
    `_at_entry`. Named anything else, it is invisible here -- which is worse
    than not collecting it, since the data looks present and is never examined.
    This fails on any column that is neither an entry metric nor a declared
    outcome, so a new one has to be named by the convention or listed on
    purpose."""
    try:
        stray = sweep.unclassified_columns("late_bonding")
    except sqlite3.OperationalError:
        pytest.skip("shadow database not present in this environment")

    assert not stray, (
        f"colonnes non classees: {stray} -- renomme en `<nom>_at_entry` pour "
        f"qu'elles soient balayees, ou ajoute-les a OUTCOME_COLUMNS si ce sont "
        f"des resultats"
    )


@pytest.mark.parametrize("pocket", sorted(sweep.POCKETS))
def test_every_registered_pocket_names_a_real_table(pocket):
    """A pocket in the registry whose table was renamed would report zero
    closures and read as 'nothing to find' rather than as a broken lookup."""
    con = sqlite3.connect(f"file:{sweep.DEFAULT_DB}?mode=ro", uri=True)
    try:
        found = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (sweep.POCKETS[pocket],),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pytest.skip("shadow database not present in this environment")
    finally:
        con.close()
    assert found == 1, f"{pocket} points at a missing table: {sweep.POCKETS[pocket]}"


class TestCorruptDataIsRefusedBeforeAnyVerdict:
    """22/08 -- the sweep reported a clean candidate on FRESH-LAUNCH while its
    whole sample came from a single day. A confident answer about broken data
    is the most dangerous output this module can produce.

    The reserve ceiling this class first shipped with was REMOVED after being
    disproved: FRESH-LAUNCH's 485,976,559$ reserve matches DexScreener's
    485,534,583$ on Orca for the same token. Real pools reach hundreds of
    millions, so corruption is detected by a row DISAGREEING WITH ITSELF, never
    by a value being large."""

    def test_a_peak_that_contradicts_its_own_row_blocks_the_verdict(self, tmp_path):
        """The real signature, from the four known-bad SUPPORT-BOUNCE rows:
        peak 7.38$ against an entry of 0.00121$ AND a last price of 0.001478$.
        A genuine pump carries the last price with it; a bad tick does not."""
        rows = _real_signal_rows()
        rows.append(_row("21", 1.0, 6000.0, peak=7.38, entry=0.00121, last=0.001478))

        db = _make_db(tmp_path, rows)
        report = sweep.build_report("late_bonding", db_path=db)

        assert report["verdict"] == "corrupt_data"
        assert report["data_health"]["incoherent_peak"] == 1

    def test_a_genuine_pump_is_not_mistaken_for_corruption(self, tmp_path):
        """A x150 move where the last price followed is a real run, not a bad
        read -- rejecting it would lose exactly the rare winners that carry
        this dome's entire PnL."""
        rows = _real_signal_rows()
        rows.append(_row("21", 150.0, 6000.0, peak=0.15, entry=0.001, last=0.148))

        db = _make_db(tmp_path, rows)
        health = sweep.build_report("late_bonding", db_path=db)["data_health"]

        assert health["incoherent_peak"] == 0

    def test_a_huge_reserve_is_never_treated_as_corruption(self, tmp_path):
        """485,976,559$ was recorded by FRESH-LAUNCH and independently
        confirmed by DexScreener at 485,534,583$ on Orca. Judging a reserve by
        its size alone rejects genuine observations."""
        rows = _real_signal_rows()
        rows.append(_row("21", 1.0, 485_976_559.0))

        db = _make_db(tmp_path, rows)
        report = sweep.build_report("late_bonding", db_path=db)

        assert report["data_health"]["clean"] is True
        assert report["verdict"] != "corrupt_data"

    def test_an_impossible_multiplier_blocks_the_verdict(self, tmp_path):
        """SUPPORT-BOUNCE v1/v2 carry multipliers around x5794 -- a corrupted
        peak baked into the trailing-stop fill price."""
        rows = _real_signal_rows()
        rows.append(_row("21", 5794.0, 6000.0))

        db = _make_db(tmp_path, rows)
        report = sweep.build_report("late_bonding", db_path=db)

        assert report["verdict"] == "corrupt_data"
        assert report["data_health"]["absurd_multiplier"] == 1

    def test_a_single_day_cannot_claim_temporal_stability(self, tmp_path):
        """A filter fitted to one day describes that day. Reporting it as
        stable is worse than not checking, because it carries a verdict."""
        rows = []
        for i in range(200):
            rows.append(_row("19", 0.70, 1000.0 + i * 10))
            rows.append(_row("19", 1.60, 6000.0 + i * 10))

        db = _make_db(tmp_path, rows)
        report = sweep.build_report("late_bonding", db_path=db)

        assert report["verdict"] == "single_day"
        assert report["data_health"]["distinct_days"] == 1
        assert not report["survivors"], "no candidate may survive on one day"

    def test_clean_data_still_reaches_a_verdict(self, tmp_path):
        """The guard must not swallow the real case it was added around."""
        db = _make_db(tmp_path, _real_signal_rows())
        report = sweep.build_report("late_bonding", db_path=db)

        assert report["data_health"]["clean"] is True
        assert report["verdict"] == "candidate_found"


class TestTheReportAlwaysRenders:
    """A report that crashes while printing is a report nobody reads. Caught
    live 22/08: the corruption banner formatted BOTH worst-values even when
    only one kind of fault was present, so the tool died on the exact pockets
    it had just been taught to flag."""

    @pytest.mark.parametrize("bad_row", [
        pytest.param({"mult": 1.0, "reserve": 6000.0, "peak": 7.38,
                      "entry": 0.00121, "last": 0.001478}, id="incoherent_peak_only"),
        pytest.param({"mult": 5794.0, "reserve": 6000.0}, id="absurd_multiplier_only"),
    ])
    def test_a_single_kind_of_corruption_still_prints(self, tmp_path, bad_row):
        rows = _real_signal_rows()
        rows.append(_row("21", **bad_row))

        report = sweep.build_report("late_bonding", db_path=_make_db(tmp_path, rows))
        rendered = sweep._render(report)

        assert "DONNEES CORROMPUES" in rendered

    def test_a_clean_report_prints_without_a_warning(self, tmp_path):
        report = sweep.build_report(
            "late_bonding", db_path=_make_db(tmp_path, _real_signal_rows())
        )
        rendered = sweep._render(report)

        assert "DONNEES CORROMPUES" not in rendered
        assert "UN SEUL JOUR" not in rendered

    def test_a_single_day_report_prints_its_warning(self, tmp_path):
        rows = [_row("19", 1.6, 6000.0 + i) for i in range(120)]
        rows += [_row("19", 0.7, 1000.0 + i) for i in range(120)]

        report = sweep.build_report("late_bonding", db_path=_make_db(tmp_path, rows))

        assert "UN SEUL JOUR" in sweep._render(report)
