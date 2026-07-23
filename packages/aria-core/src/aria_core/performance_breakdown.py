"""Performance-breakdown analysis for the paper-trading portfolio (07/23,
operator request: "balance toutes tes idees qui permettrons daffirmer les
mauvaise et les meilleurs resultat" -- segment winrate/PnL by ANY decision
factor to find what actually works, not just a single global winrate).

Two layers, deliberately kept separate:
  1. Pure, DB-free functions (``compute_metrics``, ``breakdown_by``, the
     ``key_*`` functions) -- take a plain list of trade dicts, return plain
     dicts. Trivially unit-testable, no event loop / no aiosqlite needed.
  2. ``get_all_closed_trades`` -- the only function that touches the DB,
     combining ``paper_trader.get_closed_positions`` (current cycle) with
     every past weekly cycle archived in ``paper_position_archive`` (never
     lost at a weekly reset, see ``paper_trader.run_weekly_reset``) -- so the
     breakdown always covers the FULL track record, not just the current
     week.

Winrate alone is a poor signal (operator's own example: 100% winrate on
$1,000 total PnL can be worse than 75% winrate on $3,000) -- every group
below carries winrate AND profit factor AND expectancy per trade, never
winrate in isolation."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from aria_core.paper_trader import get_archived_closed_positions, get_closed_positions


def _rr_from_levels(entry: float | None, target: float | None, invalidation: float | None) -> float | None:
    """Recomputes R/R from the persisted price levels -- used only as a
    fallback for trades opened before the ``rr`` column existed (07/23).
    ``None`` on any missing/invalid level, never an invented ratio."""
    if not entry or not target or not invalidation:
        return None
    risk = entry - invalidation
    if risk <= 0:
        return None
    return (target - entry) / risk


async def get_all_closed_trades(limit: int = 5000) -> list[dict]:
    """Every closed paper-trading position: the current cycle
    (``paper_position``, via ``get_closed_positions``) PLUS every past weekly
    cycle already archived (``paper_position_archive``, via
    ``get_archived_closed_positions``) -- the breakdown must cover the full
    track record, not just the week in progress. ``limit`` applies to EACH
    source independently (current cycle rarely holds more than a few dozen;
    the archive can grow indefinitely over many weeks)."""
    current = await get_closed_positions(limit=limit)
    archived = await get_archived_closed_positions(limit=limit)
    return current + archived


# ── pure metrics (no DB, no event loop) ─────────────────────────────────────


def compute_metrics(trades: list[dict]) -> dict:
    """Core performance metrics for a list of already-closed trades.
    Everything beyond winrate the operator asked for explicitly:
      - ``winrate``: fraction of trades with ``pnl_usd > 0`` (0.0 on empty input)
      - ``pnl_total``: sum of ``pnl_usd`` (0.0 on empty input, never invented)
      - ``profit_factor``: sum of gains / sum of losses (``None`` if there are
        no losing trades -- a ratio against zero has no meaning, not "infinite")
      - ``avg_win`` / ``avg_loss``: mean PnL of winning / losing trades
        separately (``None`` if that side has zero trades)
      - ``expectancy``: winrate*avg_win - (1-winrate)*avg_loss -- the single
        number an operator's example directly maps to (75% winrate at $3,000
        total beating 100% winrate at $1,000 is exactly a higher expectancy
        driven by a much larger avg_win, not the winrate itself). Well-defined
        even at 100% or 0% winrate (the missing side's weight is exactly 0,
        so a missing ``avg_win``/``avg_loss`` is treated as 0 rather than
        making the whole expectancy unavailable) -- ``None`` only when there
        is no ``pnl_usd`` data at all.
      - ``max_drawdown_usd``: the worst cumulative PnL drop from a running
        peak, over trades ORDERED by ``closed_at`` -- a real portfolio-wide
        drawdown ONLY when ``trades`` is the full unsegmented history; for a
        segmented subset it's a "what-if this had been the only strategy
        traded" figure, still useful to compare segments' volatility, but not
        the actual capital drawdown experienced (other segments' trades
        happened interleaved in real time).

    Missing/unparsable ``pnl_usd``/``closed_at`` on a trade excludes it from
    the relevant calculation rather than crashing or inventing a value."""
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0, "winrate": 0.0, "pnl_total": 0.0, "profit_factor": None,
            "avg_win": None, "avg_loss": None, "expectancy": None, "max_drawdown_usd": 0.0,
        }

    pnls = [t.get("pnl_usd") for t in trades if t.get("pnl_usd") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n_with_pnl = len(pnls)

    winrate = (len(wins) / n_with_pnl) if n_with_pnl else 0.0
    pnl_total = sum(pnls)
    gross_win = sum(wins)
    gross_loss = -sum(losses)  # positive number
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    avg_win = (gross_win / len(wins)) if wins else None
    avg_loss = (gross_loss / len(losses)) if losses else None
    if n_with_pnl == 0:
        expectancy = None
    else:
        # A 100%-winrate (or 0%-winrate) sample has no losing (or winning)
        # side at all, but the formula is still well-defined: the missing
        # side's weight (1-winrate or winrate) is exactly 0, so it's treated
        # as 0 here rather than making the whole expectancy unavailable.
        expectancy = winrate * (avg_win or 0.0) - (1 - winrate) * (avg_loss or 0.0)

    dated = [
        t for t in trades
        if t.get("closed_at") and t.get("pnl_usd") is not None
    ]
    dated.sort(key=lambda t: t["closed_at"])
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in dated:
        cumulative += t["pnl_usd"]
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    return {
        "n_trades": n,
        "winrate": winrate,
        "pnl_total": pnl_total,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "max_drawdown_usd": max_dd,
    }


def breakdown_by(trades: list[dict], key_fn: Callable[[dict], Any]) -> dict[Any, dict]:
    """Groups ``trades`` by ``key_fn(trade)`` and computes ``compute_metrics``
    for EACH group independently -- the generic tool behind every
    segmentation below (and any future one: a new idea is a new one-line
    ``key_fn``, never a new report to build from scratch). A trade for which
    ``key_fn`` returns ``None`` is dropped from the breakdown entirely (never
    silently merged into a misleading catch-all group) -- callers that want
    an explicit "unknown" bucket must return that string from their own
    ``key_fn`` instead of ``None``."""
    groups: dict[Any, list[dict]] = defaultdict(list)
    for t in trades:
        key = key_fn(t)
        if key is None:
            continue
        groups[key].append(t)
    return {key: compute_metrics(group_trades) for key, group_trades in groups.items()}


# ── ready-made segmentation keys ────────────────────────────────────────────


def key_conviction_tier(t: dict) -> str:
    return t.get("conviction_tier") or "unknown"


def key_chain(t: dict) -> str:
    return t.get("chain") or "unknown"


def key_regime(t: dict) -> str:
    return t.get("entry_regime") or "unknown"


def key_exit_reason(t: dict) -> str:
    return t.get("close_reason") or "unknown"


def key_discovery_channel(t: dict) -> str:
    return t.get("discovery_channel") or "unknown"


def _bucket(value: float | None, edges: list[tuple[float, str]], *, above: str) -> str:
    """Shared helper for every numeric bucket below: ``edges`` is a list of
    ``(upper_bound, label)`` pairs, checked in order -- ``above`` is the label
    for anything past the last edge. ``"unknown"`` if ``value`` is ``None``."""
    if value is None:
        return "unknown"
    for upper, label in edges:
        if value < upper:
            return label
    return above


def key_rr(t: dict) -> str:
    rr = t.get("rr")
    if rr is None:
        rr = _rr_from_levels(t.get("entry_price"), t.get("target_price"), t.get("invalidation_price"))
    return _bucket(rr, [(2.0, "<2.0"), (2.5, "2.0-2.5"), (3.5, "2.5-3.5")], above=">=3.5")


def key_rvol(t: dict) -> str:
    return _bucket(t.get("rvol_multiple"), [(5.0, "<5x"), (10.0, "5-10x"), (20.0, "10-20x")], above=">=20x")


def key_align_score(t: dict) -> str:
    score = t.get("align_score")
    return "unknown" if score is None else str(score)


def key_atr(t: dict) -> str:
    """ATR as fraction of entry price (e.g. 0.15 = 15%) -- same buckets as
    the trailing-stop width clamp already used elsewhere (5%-40%)."""
    atr = t.get("entry_atr_pct")
    return _bucket(atr, [(0.10, "<10%"), (0.20, "10-20%"), (0.30, "20-30%")], above=">=30%")


def key_liquidity(t: dict) -> str:
    return _bucket(
        t.get("entry_liquidity_usd"),
        [(50_000, "<50k$"), (100_000, "50-100k$"), (300_000, "100-300k$")],
        above=">=300k$",
    )


def key_dev_sold(t: dict) -> str:
    pct = t.get("entry_dev_sold_pct")
    return _bucket(pct, [(0.10, "<10%"), (0.30, "10-30%"), (0.60, "30-60%")], above=">=60%")


def _parsed_opened_at(t: dict) -> datetime | None:
    raw = t.get("opened_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def key_hour_of_day(t: dict) -> str:
    dt = _parsed_opened_at(t)
    return "unknown" if dt is None else f"{dt.hour:02d}h-{(dt.hour + 1) % 24:02d}h"


_WEEKDAY_LABELS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def key_day_of_week(t: dict) -> str:
    dt = _parsed_opened_at(t)
    return "unknown" if dt is None else _WEEKDAY_LABELS[dt.weekday()]


# ── every ready-made segmentation, run together by the Telegram command ────

ALL_BREAKDOWNS: dict[str, Callable[[dict], str]] = {
    "Palier de conviction": key_conviction_tier,
    "Chaîne": key_chain,
    "Régime macro à l'entrée": key_regime,
    "Mécanisme de sortie": key_exit_reason,
    "Canal de découverte": key_discovery_channel,
    "R/R initial": key_rr,
    "Volume relatif (RVOL)": key_rvol,
    "Signaux techniques alignés": key_align_score,
    "Volatilité (ATR) à l'entrée": key_atr,
    "Liquidité à l'entrée": key_liquidity,
    "% vendu par le déployeur à l'entrée": key_dev_sold,
    "Heure d'entrée (UTC)": key_hour_of_day,
    "Jour de la semaine": key_day_of_week,
}


def _fmt_pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"


def _fmt_money(value: float | None) -> str:
    return "?" if value is None else f"{value:,.0f}$"


def _fmt_ratio(value: float | None) -> str:
    return "?" if value is None else f"{value:.2f}"


def format_breakdown_report(trades: list[dict]) -> str:
    """Full Telegram report: global metrics first, then every segmentation in
    ``ALL_BREAKDOWNS``, groups sorted by expectancy (best first) so the most
    useful signal reads at the top, not buried alphabetically. A dimension
    where every trade lacks the underlying field entirely (e.g. old trades
    with no ``conviction_tier``) still shows an honest single "unknown"
    group, never silently hidden."""
    if not trades:
        return "📊 Performance breakdown\n\nAucun trade clôturé pour l'instant."

    g = compute_metrics(trades)
    lines = [
        "📊 Performance breakdown",
        "",
        f"Trades : {g['n_trades']} · Winrate : {_fmt_pct(g['winrate'])}",
        f"PnL total : {_fmt_money(g['pnl_total'])} · Profit factor : {_fmt_ratio(g['profit_factor'])}",
        f"Gain moyen : {_fmt_money(g['avg_win'])} · Perte moyenne : {_fmt_money(g['avg_loss'])}",
        f"Espérance/trade : {_fmt_money(g['expectancy'])} · Drawdown max : {_fmt_money(g['max_drawdown_usd'])}",
    ]

    for label, key_fn in ALL_BREAKDOWNS.items():
        groups = breakdown_by(trades, key_fn)
        if not groups:
            continue
        lines.append("")
        lines.append(f"— {label} —")
        ranked = sorted(
            groups.items(),
            key=lambda kv: (kv[1]["expectancy"] if kv[1]["expectancy"] is not None else float("-inf")),
            reverse=True,
        )
        for group_key, m in ranked:
            lines.append(
                f"  {group_key} : {m['n_trades']} trades · winrate {_fmt_pct(m['winrate'])}"
                f" · PnL {_fmt_money(m['pnl_total'])} · espérance {_fmt_money(m['expectancy'])}"
            )

    return "\n".join(lines)
