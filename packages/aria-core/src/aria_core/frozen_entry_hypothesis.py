"""Entry hypotheses FROZEN with their expected numbers, then scored against
data they have never seen.

**Why this exists (22/08, operator-directed: "fige le candidat A et teste le
maintenant").** In one session, five entry filters looked strong in analysis
and four of them evaporated on inspection -- a +25% profit floor, an early
profit rung, a low-activity segment, a concentration ceiling. Every one of them
was found by searching an already-observed sample, which is exactly how
searching thousands of combinations produces something that fits noise.

The defence is not a better search. It is to WRITE THE PREDICTION DOWN FIRST,
with the numbers it claims, and then let new closures judge it. A hypothesis
that cannot be stated before the data arrives is not a hypothesis.

So this module is deliberately NOT a search tool -- `pocket_entry_sweep` is
that, and its output is a source of candidates, never a conclusion. Here each
candidate is pinned in code with the sample it came from, and `evaluate()`
reports how it actually did. Nothing is applied automatically: a hypothesis
that survives becomes a proposal to the operator, never a filter that switched
itself on.

Usage::

    python -m aria_core.frozen_entry_hypothesis
    python -m aria_core.frozen_entry_hypothesis --since 2026-08-22T13:00
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

DEFAULT_DB = "/opt/aria-data/shadow.db"
TABLE = "solana_late_bonding_shadow_log"

# A verdict needs enough closures on BOTH sides of the split. Below this the
# answer is "not yet", never a number -- the whole point of the module is to
# stop reading small samples as results.
MIN_CLOSURES_PER_SIDE = 60

# How far the live result may fall short of the frozen claim and still count as
# holding. Generous on purpose: a hypothesis is confirmed by its DIRECTION and
# rough size, not by reproducing a decimal. Tighter than this and every real
# signal reads as a failure.
TOLERANCE_PCT = 5.0


@dataclass(frozen=True)
class Hypothesis:
    """One frozen claim. `predicate` decides which closures it selects."""

    name: str
    stated_on: str
    rationale: str
    predicate: object
    # What it claimed on the sample it was FOUND on -- never updated afterwards.
    # Editing these to match a live result would defeat the entire mechanism.
    expected_avg_pct: float
    expected_without_top5_pct: float
    expected_baseline_without_top5_pct: float
    found_on_closures: int
    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def expected_edge_pct(self) -> float:
        return self.expected_without_top5_pct - self.expected_baseline_without_top5_pct


def _first_entry_only(row: dict) -> bool:
    """The re-entry cooldown is already live in production, so every hypothesis
    below is stated on first entries only -- measuring against re-entries would
    credit a filter for a rule the pocket already applies. Measured: first
    entries return +18.61% outlier-tested, re-entries +1.15%."""
    return row.get("_rank") == 1


CANDIDATE_A = Hypothesis(
    name="A",
    stated_on="2026-08-22",
    rationale=(
        "On first entries, keep only tokens where the biggest buyer holds AT "
        "LEAST 4.2% of buy volume and the pool holds more than 6088$. The "
        "concentration floor is deliberately a MINIMUM, which is "
        "counter-intuitive: a token nobody takes a real position in goes "
        "nowhere. The reserve floor sits above the 5500$ hard gate, so this is "
        "a preference for the deeper half of what is already tradable."
    ),
    predicate=lambda r: (
        _first_entry_only(r)
        and (r.get("top_buyer_share_at_entry") or 0) > 0.042
        and (r.get("reserve_usd") or 0) > 6088
    ),
    expected_avg_pct=41.19,
    expected_without_top5_pct=28.61,
    expected_baseline_without_top5_pct=18.61,
    found_on_closures=670,
    caveats=(
        "Fails the permutation test once corrected for the ~1800 combinations "
        "searched (p*1800 = 0.090 against a 0.05 threshold) -- it is the "
        "closest any candidate came, not a pass.",
        "Holds in cross-validation but with a shrinking edge (+13.13 on the "
        "first half, +2.52 on the second).",
        "Found on 2 days of data, 830 of 1029 closures coming from one of them.",
        "Cuts roughly 70% of the flow: 4.0 trades/hour against 9.3 for the "
        "socle. The cost is real and paid on every rejected winner.",
    ),
)

CANDIDATE_C = Hypothesis(
    name="C",
    stated_on="2026-08-22",
    rationale=(
        "On first entries, at most 260 distinct buyers and a pool at or below "
        "7690$. Less selective than A and built from the opposite reserve "
        "direction, which is precisely why it is worth carrying alongside: if "
        "both survive, the reserve is not what either is really measuring."
    ),
    predicate=lambda r: (
        _first_entry_only(r)
        and (r.get("distinct_buyers_at_entry") or 0) <= 260
        and (r.get("reserve_usd") or 0) <= 7690
    ),
    expected_avg_pct=28.47,
    expected_without_top5_pct=21.12,
    expected_baseline_without_top5_pct=18.61,
    found_on_closures=670,
    caveats=(
        "Its reserve condition points the OPPOSITE way to A's. Both cannot be "
        "measuring the same underlying thing, and both surviving would suggest "
        "neither is measuring the reserve at all.",
    ),
)

HYPOTHESES = (CANDIDATE_A, CANDIDATE_C)


def load_closures(*, db_path: str = DEFAULT_DB, since: str | None = None,
                  table: str = TABLE) -> list[dict]:
    """Closed positions, ordered, each tagged with its rank on its own token."""
    sql = (
        f"SELECT * FROM {table} "  # noqa: S608 -- table is a module constant
        f"WHERE final_multiplier IS NOT NULL "
        f"AND (exit_reason IS NULL OR exit_reason NOT IN "
        f"('buy_never_landed','config_error_excluded'))"
    )
    params: list = []
    if since:
        sql += " AND detected_at >= ?"
        params.append(since)
    sql += " ORDER BY detected_at"

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()

    seen: dict[str, int] = defaultdict(int)
    for row in rows:
        seen[row["token_address"]] += 1
        row["_rank"] = seen[row["token_address"]]
    return rows


def _pnl(row: dict) -> float:
    return (row["final_multiplier"] - 1.0) * 100.0


def score(rows: list[dict]) -> dict:
    """Average, and the same average with the best five removed.

    The second number is the one that matters: in this dome roughly 1.8% of
    trades carry the whole gain, so an unadjusted average says more about which
    outliers landed in the sample than about the strategy."""
    values = sorted((_pnl(r) for r in rows), reverse=True)
    n = len(values)
    if n == 0:
        return {"n": 0, "avg_pct": 0.0, "without_top5_pct": 0.0, "winrate_pct": 0.0}
    total = sum(values)
    return {
        "n": n,
        "avg_pct": round(total / n, 2),
        "without_top5_pct": round((total - sum(values[:5])) / (n - 5), 2) if n > 5 else 0.0,
        "winrate_pct": round(sum(1 for v in values if v > 0) / n * 100, 1),
    }


def evaluate(hypothesis: Hypothesis, rows: list[dict]) -> dict:
    """Scores one frozen hypothesis against closures it never saw."""
    kept = [r for r in rows if hypothesis.predicate(r)]
    rejected = [r for r in rows if not hypothesis.predicate(r)]
    kept_score, rejected_score = score(kept), score(rejected)

    enough = len(kept) >= MIN_CLOSURES_PER_SIDE and len(rejected) >= MIN_CLOSURES_PER_SIDE
    observed_edge = kept_score["without_top5_pct"] - rejected_score["without_top5_pct"]

    if not enough:
        verdict = "insufficient"
    elif observed_edge >= hypothesis.expected_edge_pct - TOLERANCE_PCT:
        verdict = "holds"
    elif observed_edge > 0:
        verdict = "weakened"
    else:
        verdict = "broken"

    return {
        "hypothesis": hypothesis.name,
        "stated_on": hypothesis.stated_on,
        "kept": kept_score,
        "rejected": rejected_score,
        "expected_edge_pct": round(hypothesis.expected_edge_pct, 2),
        "observed_edge_pct": round(observed_edge, 2),
        "verdict": verdict,
        "caveats": list(hypothesis.caveats),
    }


def build_report(*, db_path: str = DEFAULT_DB, since: str | None = None,
                 table: str = TABLE) -> dict:
    rows = load_closures(db_path=db_path, since=since, table=table)
    return {
        "closures_seen": len(rows),
        "since": since,
        "results": [evaluate(h, rows) for h in HYPOTHESES],
    }


def _render(report: dict) -> str:
    lines = [
        f"HYPOTHESES GELEES -- testees sur {report['closures_seen']} cloture(s)"
        + (f" depuis {report['since']}" if report.get("since") else ""),
    ]
    for res in report["results"]:
        k, rj = res["kept"], res["rejected"]
        lines.append(
            f"\n  {res['hypothesis']} (gelee le {res['stated_on']}) -> {res['verdict'].upper()}"
        )
        lines.append(
            f"     gardes  n={k['n']:4d}  moy={k['avg_pct']:+7.2f}%  "
            f"sans_top5={k['without_top5_pct']:+7.2f}%  winrate={k['winrate_pct']:.0f}%"
        )
        lines.append(
            f"     rejetes n={rj['n']:4d}  moy={rj['avg_pct']:+7.2f}%  "
            f"sans_top5={rj['without_top5_pct']:+7.2f}%  winrate={rj['winrate_pct']:.0f}%"
        )
        lines.append(
            f"     avantage annonce {res['expected_edge_pct']:+.2f} pts, "
            f"observe {res['observed_edge_pct']:+.2f} pts"
        )
        if res["verdict"] == "insufficient":
            lines.append(f"     (il faut {MIN_CLOSURES_PER_SIDE} clotures de chaque cote)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None,
                        help="ISO timestamp; only closures detected after it")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--table", default=TABLE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(db_path=args.db, since=args.since, table=args.table)
    print(json.dumps(report, indent=2) if args.json else _render(report))


if __name__ == "__main__":
    main()
