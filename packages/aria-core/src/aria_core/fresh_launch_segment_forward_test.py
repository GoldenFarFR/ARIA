"""Forward-test observer for the ">=6000$ reserve + top_holder <92%" segment
of the Solana fresh-launch pockets (20/08, operator-directed).

**Why this is an observer and not a pocket.** The 20/08 emergency performance
investigation found exactly one profitable profile in the whole fresh-launch
dome: FAST-DISCOVERY closures with an entry reserve >=6000$ and a RugCheck
top_holder below the reject threshold showed +10.57% average PnL at a 20.5%
winrate (n=146) while every other segment bled. The operator's explicit call
was NOT to pivot the pockets onto that profile, but to track it separately
until a bigger sample either confirms or kills it. So nothing here changes a
single entry, exit, threshold or subscription -- this module only ever READS
the pockets' own closure rows.

**Why the start cutoff matters (the whole methodological point).** The 146
closures that produced the +10.57% figure are the SAME data the hypothesis was
found in. Measuring the segment against them again would just re-confirm the
overfit. ``SEGMENT_FORWARD_TEST_START`` freezes the moment the hypothesis was
formed; every function here reports only on closures DETECTED at or after that
instant, so the verdict is built on data the hypothesis has never seen. The
in-sample figures are kept in ``IN_SAMPLE_BASELINE`` purely as the number the
forward result has to be compared against, never mixed into it.

**Why a control group is reported alongside.** A segment looking good in a
market that lifted everything is not evidence. Every report returns the
segment AND the same-window non-segment closures from the same pocket, so the
comparison is like-for-like on the same hours of the same market.

Only FAST-DISCOVERY can populate this segment: WS-EXIT abandons any candidate
reaching ``MAX_LIQUIDITY_USD_ENTRY``=5000$, so it structurally never enters
above 6000$. Kept as a parameter rather than hardcoded so the pockets scanned
stay explicit at the call site.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import aiosqlite

from .paths import shadow_db_path

# Frozen at the instant the hypothesis was formed -- see the module docstring
# on why the in-sample closures must be excluded from the verdict.
SEGMENT_FORWARD_TEST_START = "2026-08-20T20:00:00+00:00"

# The segment's own definition, straight from the 20/08 finding.
SEGMENT_MIN_RESERVE_USD = 6000.0
def _segment_max_top_holder_pct() -> float:
    """The live traction threshold, IMPORTED rather than copied.

    20/08 -- was hardcoded to 92.0 and went stale the moment the real threshold
    was recalibrated to 80.0 on 1496 closures, so this observer was silently
    measuring a segment the pockets no longer trade. Read lazily to keep this
    module importable without pulling a pocket in at module load.
    """
    from .solana_fresh_launch_ws_exit_shadow import HOLDER_CONCENTRATION_REJECT_PCT

    return float(HOLDER_CONCENTRATION_REJECT_PCT)

# The in-sample numbers this forward test exists to confirm or kill. NEVER
# merged into a forward result -- kept only as the comparison baseline.
IN_SAMPLE_BASELINE = {"n": 146, "winrate_pct": 20.5, "avg_pnl_pct": 10.57}

# Both pockets' tables. WS-EXIT can never populate the segment (5000$ entry
# ceiling) but is scanned anyway so that assumption stays continuously
# verified rather than assumed -- if it ever starts showing rows here, an
# entry-band change slipped in unnoticed.
POCKET_TABLES = {
    "fast_discovery": "solana_fresh_launch_fast_discovery_shadow_log",
    "ws_exit": "solana_fresh_launch_ws_exit_shadow_log",
}


@dataclass
class SegmentStats:
    """One measured group. ``avg_pnl_pct`` uses the realistic multiplier
    (slippage + price impact applied) whenever it exists, falling back to the
    nominal one -- same convention as every PnL figure quoted in this dome."""

    label: str
    n: int = 0
    winrate_pct: float | None = None
    avg_pnl_pct: float | None = None
    median_pnl_pct: float | None = None
    worst_pnl_pct: float | None = None
    best_pnl_pct: float | None = None


@dataclass
class ForwardTestReport:
    pocket: str
    since: str
    segment: SegmentStats
    control: SegmentStats
    baseline: dict = field(default_factory=lambda: dict(IN_SAMPLE_BASELINE))

    @property
    def verdict(self) -> str:
        """Deliberately descriptive, never prescriptive -- this module reports,
        the operator decides (cf. the Auto-Pivot rule: a dead end is an
        analysis conclusion, never an autonomous shutdown)."""
        if self.segment.n < 30:
            return f"echantillon insuffisant ({self.segment.n}/30 cloture(s))"
        if self.segment.avg_pnl_pct is None or self.control.avg_pnl_pct is None:
            return "donnees incompletes"
        edge = self.segment.avg_pnl_pct - self.control.avg_pnl_pct
        if self.segment.avg_pnl_pct > 0 and edge > 0:
            return f"confirme jusqu'ici (+{edge:.2f} pt vs temoin)"
        if edge > 0:
            return f"meilleur que le temoin (+{edge:.2f} pt) mais toujours negatif"
        return f"non confirme ({edge:+.2f} pt vs temoin)"


def _stats_sql(table: str, *, segment: bool) -> str:
    """`top_holder IS NULL` counts as OUTSIDE the segment on purpose: an
    unenriched row is not evidence the token was clean, and a forward test
    that quietly credits itself with unknowns proves nothing."""
    if segment:
        holder_clause = "rugcheck_top_holder_pct IS NOT NULL AND rugcheck_top_holder_pct < ?"
        reserve_clause = "reserve_usd >= ?"
    else:
        holder_clause = "(rugcheck_top_holder_pct IS NULL OR rugcheck_top_holder_pct >= ?)"
        reserve_clause = "(reserve_usd IS NULL OR reserve_usd < ?)"
    joiner = " AND " if segment else " OR "
    return f"""
        SELECT COALESCE(realistic_final_multiplier, final_multiplier) - 1.0 AS pnl
        FROM {table}
        WHERE exit_reason IS NOT NULL AND exit_reason <> ''
          AND detected_at >= ?
          AND COALESCE(realistic_final_multiplier, final_multiplier) IS NOT NULL
          AND ({reserve_clause}{joiner}{holder_clause})
    """


def _summarise(label: str, pnls: list[float]) -> SegmentStats:
    if not pnls:
        return SegmentStats(label=label, n=0)
    ordered = sorted(pnls)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    wins = sum(1 for p in pnls if p > 0)
    return SegmentStats(
        label=label,
        n=len(pnls),
        winrate_pct=round(100.0 * wins / len(pnls), 1),
        avg_pnl_pct=round(100.0 * sum(pnls) / len(pnls), 2),
        median_pnl_pct=round(100.0 * median, 2),
        worst_pnl_pct=round(100.0 * ordered[0], 2),
        best_pnl_pct=round(100.0 * ordered[-1], 2),
    )


async def build_report(
    pocket: str = "fast_discovery",
    *,
    since: str = SEGMENT_FORWARD_TEST_START,
    db_path: str | None = None,
) -> ForwardTestReport:
    """Reads one pocket's closures since ``since`` and splits them into the
    segment and its same-window control group. Never writes anything."""
    table = POCKET_TABLES.get(pocket)
    if table is None:
        # The raw value is deliberately NOT echoed back: this reaches an HTTP
        # endpoint whose `pocket` query parameter is caller-controlled, and
        # reflecting arbitrary caller input into an error message that is
        # returned AND logged is how a reflection/log-injection issue starts.
        # The allowlist itself is safe to name -- it is three fixed constants.
        raise ValueError(f"unknown pocket -- expected one of {sorted(POCKET_TABLES)}")

    path = db_path or str(shadow_db_path())
    groups: dict[bool, list[float]] = {True: [], False: []}
    async with aiosqlite.connect(path) as db:
        for is_segment in (True, False):
            try:
                cur = await db.execute(
                    _stats_sql(table, segment=is_segment),
                    (since, SEGMENT_MIN_RESERVE_USD, _segment_max_top_holder_pct()),
                )
            except aiosqlite.OperationalError:
                # Table not created yet (a pocket that has never run) -- an
                # empty report is the honest answer, never a crash.
                break
            groups[is_segment] = [row[0] for row in await cur.fetchall() if row[0] is not None]

    return ForwardTestReport(
        pocket=pocket,
        since=since,
        segment=_summarise(f">=6000$ + top_holder<92% ({pocket})", groups[True]),
        control=_summarise(f"temoin, meme fenetre ({pocket})", groups[False]),
    )


def format_report(report: ForwardTestReport) -> str:
    """Operator-facing plain text (French, no em-dash, no emoji -- house style
    for anything the operator reads directly)."""
    lines = [
        f"Forward-test segment >=6000$ + top_holder<92% - poche {report.pocket}",
        f"Depuis {report.since}",
        "",
        f"Reference in-sample (a confirmer) : n={report.baseline['n']}, "
        f"winrate {report.baseline['winrate_pct']}%, PnL {report.baseline['avg_pnl_pct']}%",
        "",
    ]
    for stats in (report.segment, report.control):
        if not stats.n:
            lines.append(f"{stats.label} : aucune cloture sur la fenetre")
            continue
        lines.append(
            f"{stats.label} : n={stats.n}, winrate {stats.winrate_pct}%, "
            f"PnL moyen {stats.avg_pnl_pct}%, median {stats.median_pnl_pct}%, "
            f"pire {stats.worst_pnl_pct}%, meilleur {stats.best_pnl_pct}%"
        )
    lines += ["", f"Etat : {report.verdict}"]
    return "\n".join(lines)
