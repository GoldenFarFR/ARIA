"""Sweep every entry metric of a pocket for a threshold that separates
losers from winners -- and refuse to believe one until it survives three
independent robustness checks.

**Why this exists (22/08, operator-directed).** Operator, verbatim: "au
prochaine analyse va direct refaire ses meme recherche inscrit le dans ton
systeme quand il faut analyser les poches". The method below was written as a
throwaway script, found the pocket's liquidity floor was 2500$ too low, and
would otherwise have had to be re-invented -- differently, and probably worse --
at the next analysis.

**What it does that eyeballing a table does not.** Three filters had already
been proposed on this pocket from partial samples (a +25% profit floor, a
tighter stop, a low-activity entry segment). Every one of them looked strong on
90-170 closures and evaporated on the full sample. The difference was never the
idea, it was the absence of these checks:

  1. **outlier test** -- re-score without the best trade, and without the best
     two. In this dome 1.8% of trades carry 100% of the gain, so an average is
     an artefact until proven otherwise. This alone killed the three filters
     above.
  2. **temporal stability** -- the same split, day by day. A filter that only
     works on the day it was fitted is a description of that day.
  3. **monotonicity** -- score every band of the metric, not just the two sides
     of the chosen cut. A real signal is a gradient; one lucky bucket is not.

A candidate that fails any of the three is reported as such and NOT recommended,
however good its headline number.

**The operator's asymmetry (22/08), applied in the ranking.** Verbatim: "meme
si sa fait perdre 2 ou 3 trade gagnant je prefere les eviter". So candidates are
ranked on loss removed minus gain removed, not on kept-average alone -- a filter
that cuts deep into the losses is preferred even when it takes real winners with
it. `gain_lost` is always reported next to it so the price stays visible.

This module OBSERVES. It never edits a threshold: changing what a pocket buys
stays a decision, and one that touches real capital needs the operator.

Usage::

    python -m aria_core.pocket_entry_sweep late_bonding
    python -m aria_core.pocket_entry_sweep late_bonding --metric reserve_usd
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from dataclasses import dataclass, field

# Pocket -> (table, shadow db). Explicit rather than derived: a pocket that
# stops being listed is a visible deletion in review.
POCKETS = {
    "late_bonding": "solana_late_bonding_shadow_log",
    "fast_discovery": "solana_fresh_launch_fast_discovery_shadow_log",
    "fresh_launch": "solana_fresh_launch_shadow_log",
    "ws_exit": "solana_fresh_launch_ws_exit_shadow_log",
    "support_bounce": "solana_support_bounce_shadow_log",
    "support_bounce_v2": "solana_support_bounce_v2_shadow_log",
    "solana_pump": "solana_pump_shadow_log",
    "robinhood_pump": "robinhood_pump_shadow_log",
    "variants": "solana_variant_shadow_log",
}

DEFAULT_DB = "/opt/aria-data/shadow.db"

# Exit reasons that are not trades: the buy never landed, or the row was
# excluded by a config error. Scoring them would measure our own plumbing.
NON_TRADE_EXITS = ("buy_never_landed", "config_error_excluded")

# A column ending in one of these is a state of the world AT ENTRY, i.e. a
# thing we could have filtered on. Everything else on the row is an outcome,
# and filtering on an outcome is look-ahead, not a strategy.
#
# CONVENTION, and the reason it is enforced by a test (22/08, operator-directed:
# "pense a alimenter cette outil si tu ajoute des nouvelles données recuperable
# et tout"): any new pre-trade metric a pocket starts collecting is swept
# AUTOMATICALLY, with no change here, as long as its column ends in `_at_entry`.
# A column named otherwise is silently invisible to the sweep -- which is worse
# than not collecting it, because the data looks present and is never examined.
# `test_every_column_is_classified` fails on any column this module cannot
# place, so a new one must be named by the convention or listed in OUTCOME_
# COLUMNS on purpose.
ENTRY_SUFFIXES = ("_at_entry",)
ENTRY_EXTRA = ("reserve_usd", "has_paid_profile")

# Columns that describe what HAPPENED, or the row's own plumbing. Filtering on
# any of these is look-ahead. Listed explicitly so that a column belonging to
# neither list is a loud failure rather than a silent omission.
OUTCOME_COLUMNS = frozenset({
    "id", "pool_address", "token_address", "chain", "detected_at",
    "entry_price", "realistic_entry_price", "remaining_qty",
    "realized_proceeds", "realistic_realized_proceeds", "peak_price",
    "exit_reason", "exit_detail", "exit_price_source",
    "final_multiplier", "realistic_final_multiplier", "reinforced_final_multiplier",
    "last_price", "last_reserve_usd", "last_checked_at",
    "creator_address", "amm_pool_address", "buy_tx",
    "reinforce_price", "reinforce_at", "ladder_done",
})

# A candidate rejecting less than this is noise; more than this is a different
# pocket, not a filter.
MIN_REJECT_SHARE = 0.02
MAX_REJECT_SHARE = 0.45
MIN_KEPT_ROWS = 100

# Below this, a split is reported but never recommended -- the outlier test
# needs enough rows on both sides to mean anything.
MIN_ROWS_FOR_A_VERDICT = 200
MIN_ROWS_PER_DAY = 15

# Stability across time needs at least two days to mean anything. Without this
# the check passed on a SINGLE day of data and reported a candidate anyway --
# caught 22/08 on FRESH-LAUNCH, whose 439 closures all sit on 2026-08-19. A
# filter fitted to one day describes that day; calling that "temporally stable"
# is worse than not checking, because it carries a verdict.
MIN_DISTINCT_DAYS = 2

# Values no real pool or trade can produce. Their presence means the pocket's
# own recording is broken, and a sweep over broken data produces a confident
# answer about nothing -- the most dangerous output this module could have.
#
# Both ceilings come from defects seen live on 22/08: FRESH-LAUNCH holds five
# reserves between 1M$ and 1.485 BILLION dollars on freshly launched tokens,
# and SUPPORT-BOUNCE v1/v2 hold four multipliers around +500,000% -- the same
# raw-units/decimals confusion that priced a token at 1.6e-11 instead of
# 1.6e-5 on the real-money path the night before.
IMPLAUSIBLE_RESERVE_USD = 1_000_000.0
IMPLAUSIBLE_MULTIPLIER = 100.0

# A candidate must beat the unfiltered baseline by this much AFTER its two best
# trades are removed. Smaller than this and we are fitting noise: the three
# filters this module was written to catch all sat under 1 point.
MIN_EDGE_WITHOUT_TOP2_PCT = 3.0


@dataclass
class Scored:
    """One set of closures, scored the only way this dome accepts."""

    n: int
    avg_pct: float
    without_top1_pct: float
    without_top2_pct: float
    winrate_pct: float
    severe_loss_pct: float

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "avg_pct": round(self.avg_pct, 2),
            "without_top1_pct": round(self.without_top1_pct, 2),
            "without_top2_pct": round(self.without_top2_pct, 2),
            "winrate_pct": round(self.winrate_pct, 1),
            "severe_loss_pct": round(self.severe_loss_pct, 1),
        }


@dataclass
class Candidate:
    """A threshold, and everything needed to disbelieve it."""

    metric: str
    sense: str  # "min" -> keep >= cut, "max" -> keep <= cut
    cut: float
    kept: Scored
    rejected: Scored
    loss_avoided: float
    gain_lost: float
    checks: dict = field(default_factory=dict)

    @property
    def net(self) -> float:
        return self.loss_avoided - self.gain_lost

    @property
    def survives(self) -> bool:
        return all(self.checks.values())

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "sense": self.sense,
            "cut": round(self.cut, 4),
            "kept": self.kept.as_dict(),
            "rejected": self.rejected.as_dict(),
            "loss_avoided": round(self.loss_avoided, 0),
            "gain_lost": round(self.gain_lost, 0),
            "net": round(self.net, 0),
            "checks": self.checks,
            "survives": self.survives,
        }


def pnl_pct(row: dict) -> float | None:
    mult = row.get("final_multiplier")
    return None if mult is None else (float(mult) - 1.0) * 100.0


def score(rows: list[dict]) -> Scored:
    vals = sorted((v for r in rows if (v := pnl_pct(r)) is not None), reverse=True)
    n = len(vals)
    if n == 0:
        return Scored(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    total = sum(vals)
    return Scored(
        n=n,
        avg_pct=total / n,
        without_top1_pct=(total - vals[0]) / (n - 1) if n > 1 else total,
        without_top2_pct=(total - vals[0] - vals[1]) / (n - 2) if n > 2 else total,
        winrate_pct=sum(1 for v in vals if v > 0) / n * 100.0,
        severe_loss_pct=sum(1 for v in vals if v <= -20.0) / n * 100.0,
    )


def load_closures(pocket: str, *, db_path: str = DEFAULT_DB) -> list[dict]:
    table = POCKETS[pocket]
    placeholders = ",".join("?" for _ in NON_TRADE_EXITS)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"SELECT * FROM {table} "  # noqa: S608 -- table comes from POCKETS, never input
            f"WHERE final_multiplier IS NOT NULL "
            f"AND (exit_reason IS NULL OR exit_reason NOT IN ({placeholders}))",
            NON_TRADE_EXITS,
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def unclassified_columns(pocket: str, *, db_path: str = DEFAULT_DB) -> list[str]:
    """Columns the sweep can place neither as an entry metric nor as an outcome.

    Each one is a metric a pocket collects and the sweep silently ignores. This
    is what keeps the tool fed as new data starts being captured."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({POCKETS[pocket]})")]
    finally:
        con.close()
    return [
        c for c in cols
        if not c.endswith(ENTRY_SUFFIXES)
        and c not in ENTRY_EXTRA
        and c not in OUTCOME_COLUMNS
    ]


def entry_metrics(rows: list[dict]) -> list[str]:
    """Columns that describe the world BEFORE the buy, and nothing else."""
    if not rows:
        return []
    out = []
    for col in rows[0]:
        if not (col.endswith(ENTRY_SUFFIXES) or col in ENTRY_EXTRA):
            continue
        filled = sum(
            1 for r in rows
            if r.get(col) is not None and isinstance(r[col], (int, float))
        )
        if filled >= MIN_KEPT_ROWS:
            out.append(col)
    return out


def _split(rows: list[dict], metric: str, cut: float, sense: str) -> tuple[list, list]:
    """Fail-open on a missing value: a metric we could not read is never a
    reason to reject a candidate, only a reason to not filter on it."""
    kept, rejected = [], []
    for r in rows:
        v = r.get(metric)
        if v is None or not isinstance(v, (int, float)):
            kept.append(r)
        elif (float(v) >= cut) if sense == "min" else (float(v) <= cut):
            kept.append(r)
        else:
            rejected.append(r)
    return kept, rejected


def _monotonic(rows: list[dict], metric: str, sense: str) -> bool:
    """Score five bands of the metric; a real signal trends across them.

    Compares the mean of the two extreme bands rather than demanding a strictly
    ordered sequence -- with a few hundred rows per band the middle wobbles even
    when the gradient is real."""
    vals = sorted(
        float(r[metric]) for r in rows
        if r.get(metric) is not None and isinstance(r[metric], (int, float))
    )
    if len(vals) < MIN_ROWS_FOR_A_VERDICT:
        return False
    edges = [vals[int(len(vals) * q / 5)] for q in range(1, 5)]
    bands, previous = [], float("-inf")
    for edge in [*edges, float("inf")]:
        band = [r for r in rows
                if r.get(metric) is not None and isinstance(r[metric], (int, float))
                and previous <= float(r[metric]) < edge]
        if band:
            bands.append(score(band).avg_pct)
        previous = edge
    if len(bands) < 4:
        return False
    low, high = bands[0], bands[-1]
    return (high > low) if sense == "min" else (low > high)


def _stable_over_time(rows: list[dict], metric: str, cut: float, sense: str) -> bool:
    """The same split, day by day. A day where the kept side loses to the cut
    side breaks it -- that is the filter working backwards."""
    days = sorted({str(r.get("detected_at", ""))[:10] for r in rows})
    tested = 0
    for day in days:
        same_day = [r for r in rows if str(r.get("detected_at", "")).startswith(day)]
        kept, rejected = _split(same_day, metric, cut, sense)
        if len(kept) < MIN_ROWS_PER_DAY or len(rejected) < MIN_ROWS_PER_DAY:
            continue
        tested += 1
        if score(kept).avg_pct <= score(rejected).avg_pct:
            return False
    return tested >= MIN_DISTINCT_DAYS


def sweep(rows: list[dict], *, metric: str | None = None) -> list[Candidate]:
    """Every metric, every decile cut, both directions -- then the checks."""
    baseline = score(rows)
    n = len(rows)
    metrics = [metric] if metric else entry_metrics(rows)
    out: list[Candidate] = []

    for name in metrics:
        vals = sorted({
            float(r[name]) for r in rows
            if r.get(name) is not None and isinstance(r[name], (int, float))
        })
        if len(vals) < 8:
            continue
        cuts = sorted({vals[int(len(vals) * q / 20)] for q in range(1, 20)})
        for cut in cuts:
            for sense in ("min", "max"):
                kept_rows, rejected_rows = _split(rows, name, cut, sense)
                if not (n * MIN_REJECT_SHARE <= len(rejected_rows) <= n * MAX_REJECT_SHARE):
                    continue
                if len(kept_rows) < MIN_KEPT_ROWS:
                    continue
                rejected_pnls = [v for r in rejected_rows if (v := pnl_pct(r)) is not None]
                candidate = Candidate(
                    metric=name, sense=sense, cut=cut,
                    kept=score(kept_rows), rejected=score(rejected_rows),
                    loss_avoided=-sum(v for v in rejected_pnls if v < 0),
                    gain_lost=sum(v for v in rejected_pnls if v > 0),
                )
                candidate.checks = {
                    "sample": n >= MIN_ROWS_FOR_A_VERDICT,
                    "outlier": (
                        candidate.kept.without_top2_pct
                        >= baseline.without_top2_pct + MIN_EDGE_WITHOUT_TOP2_PCT
                    ),
                    "temporal": _stable_over_time(rows, name, cut, sense),
                    "monotonic": _monotonic(rows, name, sense),
                    "cuts_losses": candidate.net > 0,
                }
                out.append(candidate)

    out.sort(key=lambda c: (c.survives, c.net), reverse=True)
    return out


def data_health(rows: list[dict]) -> dict:
    """Values the pocket cannot have really observed.

    Checked BEFORE any sweep because a sweep over broken data still returns a
    confident answer, and a confident answer about corrupted numbers is the
    most dangerous thing this module could produce. Found the hard way on
    22/08: the sweep reported a clean candidate on FRESH-LAUNCH while five of
    its reserves sat above a billion dollars."""
    absurd_reserve = [
        r for r in rows
        if isinstance(r.get("reserve_usd"), (int, float))
        and float(r["reserve_usd"]) > IMPLAUSIBLE_RESERVE_USD
    ]
    absurd_multiplier = [
        r for r in rows
        if isinstance(r.get("final_multiplier"), (int, float))
        and float(r["final_multiplier"]) > IMPLAUSIBLE_MULTIPLIER
    ]
    days = {str(r.get("detected_at", ""))[:10] for r in rows if r.get("detected_at")}
    return {
        "absurd_reserve": len(absurd_reserve),
        "absurd_multiplier": len(absurd_multiplier),
        "distinct_days": len(days),
        "clean": not absurd_reserve and not absurd_multiplier,
        "worst_reserve": max(
            (float(r["reserve_usd"]) for r in absurd_reserve), default=None
        ),
        "worst_multiplier": max(
            (float(r["final_multiplier"]) for r in absurd_multiplier), default=None
        ),
    }


def build_report(pocket: str, *, metric: str | None = None,
                 db_path: str = DEFAULT_DB, top: int = 10) -> dict:
    rows = load_closures(pocket, db_path=db_path)
    baseline = score(rows)
    health = data_health(rows)
    candidates = sweep(rows, metric=metric)
    survivors = [c for c in candidates if c.survives]
    return {
        "pocket": pocket,
        "closures": len(rows),
        "baseline": baseline.as_dict(),
        "data_health": health,
        "metrics_swept": len(entry_metrics(rows)) if metric is None else 1,
        "survivors": [c.as_dict() for c in survivors[:top]],
        "best_rejected": [c.as_dict() for c in candidates if not c.survives][:top],
        # Corrupted input outranks every other outcome: a filter derived from
        # impossible numbers must never be presented as a finding, however well
        # it scores.
        "verdict": (
            "corrupt_data" if not health["clean"]
            else "insufficient" if len(rows) < MIN_ROWS_FOR_A_VERDICT
            else "single_day" if health["distinct_days"] < MIN_DISTINCT_DAYS
            else "no_filter_survives" if not survivors
            else "candidate_found"
        ),
    }


def _render(report: dict) -> str:
    lines = [
        f"POCHE {report['pocket']} -- {report['closures']} cloture(s), "
        f"{report['metrics_swept']} metrique(s) d'entree balayee(s)",
        f"  base : {report['baseline']['avg_pct']:+.2f}% "
        f"(sans top2 {report['baseline']['without_top2_pct']:+.2f}%), "
        f"{report['baseline']['severe_loss_pct']:.1f}% de pertes <=-20%",
        f"  verdict : {report['verdict']}",
    ]
    health = report["data_health"]
    if not health["clean"]:
        # Either kind can be absent, so neither worst-value may be formatted
        # unconditionally.
        faults = []
        if health["absurd_reserve"]:
            faults.append(
                f"{health['absurd_reserve']} reserve(s) absurde(s) "
                f"(max {health['worst_reserve']:,.0f}$)"
            )
        if health["absurd_multiplier"]:
            faults.append(
                f"{health['absurd_multiplier']} multiplicateur(s) absurde(s) "
                f"(max x{health['worst_multiplier']:,.0f})"
            )
        lines.append(
            f"  !! DONNEES CORROMPUES -- {', '.join(faults)}. "
            f"Rien ci-dessous n'est fiable."
        )
    elif health["distinct_days"] < MIN_DISTINCT_DAYS:
        lines.append(
            f"  !! UN SEUL JOUR de donnees ({health['distinct_days']}) -- la "
            f"stabilite temporelle ne peut pas etre testee."
        )
    for title, key in (("FILTRES QUI SURVIVENT", "survivors"),
                       ("MEILLEURS RECALES (et pourquoi)", "best_rejected")):
        lines.append(f"\n{title}")
        if not report[key]:
            lines.append("  aucun")
        for c in report[key]:
            failed = [k for k, ok in c["checks"].items() if not ok]
            lines.append(
                f"  {c['metric']} {c['sense']} {c['cut']:.4f} -> "
                f"garde {c['kept']['n']} a {c['kept']['avg_pct']:+.2f}% "
                f"(sans top2 {c['kept']['without_top2_pct']:+.2f}%), "
                f"rejette {c['rejected']['n']} a {c['rejected']['avg_pct']:+.2f}%"
            )
            lines.append(
                f"      perte evitee {c['loss_avoided']:+.0f} / "
                f"gain perdu {c['gain_lost']:+.0f} / net {c['net']:+.0f}"
                + (f"  ECHOUE: {', '.join(failed)}" if failed else "")
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pocket", choices=sorted(POCKETS))
    parser.add_argument("--metric", default=None, help="sweep a single metric")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args.pocket, metric=args.metric, db_path=args.db)
    print(json.dumps(report, indent=2) if args.json else _render(report))


if __name__ == "__main__":
    main()
