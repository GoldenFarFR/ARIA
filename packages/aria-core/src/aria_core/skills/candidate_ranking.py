"""Ranking of candidates from the screened pool — the "sorting" of mass analysis.

The crawler continuously absorbs thousands of contracts; the security filter keeps
the valid ones in ``screened_pool``. This module RANKS those kept candidates by a
TRANSPARENT composite score (every point is explainable) to surface the best candidates
for in-depth VC analysis and the track record.

This is NOT a buy decision: a priority ranking, never an order. Pure and
deterministic (same rows -> same ranking), no network call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log10

# Transparent, bounded weights. Security dominates (0..100); liquidity,
# concentration and verdict adjust at the margin. Documented and adjustable.
_VERDICT_BONUS = {"SAFE": 12.0, "CAUTION": 0.0, "DANGER": -40.0}


@dataclass(frozen=True)
class RankedCandidate:
    contract: str
    symbol: str
    rank_score: float
    security_score: int
    liquidity_usd: float
    top_holder_pct: float | None
    verdict: str
    reasons: list[str] = field(default_factory=list)


def _liquidity_points(liq: float) -> float:
    """Liquidity in points (log scale, saturating): 30k -> 0, ~100k -> +6, ~1M -> +17, cap 25."""
    if liq <= 30_000:
        return 0.0
    return min(25.0, 11.0 * log10(liq / 30_000.0))


def _concentration_points(top_holder_pct: float | None) -> float:
    """The less a holder dominates, the better. None (unknown) = neutral. Bounded -10..+10."""
    if top_holder_pct is None:
        return 0.0
    if top_holder_pct <= 10:
        return 10.0
    if top_holder_pct >= 30:
        return -10.0
    return 10.0 - (top_holder_pct - 10.0)  # linear: 10% -> +10, 30% -> -10


def score_candidate(row: dict) -> RankedCandidate:
    """Composite score for a candidate (one screened_pool row). Transparent and bounded."""
    sec = int(row.get("security_score") or 0)
    liq = float(row.get("liquidity_usd") or 0.0)
    raw_top = row.get("top_holder_pct")
    top = float(raw_top) if raw_top is not None else None
    verdict = str(row.get("verdict") or "CAUTION").upper()

    sec_pts = float(max(0, min(100, sec)))       # 0..100 (dominant)
    liq_pts = _liquidity_points(liq)             # 0..25
    conc_pts = _concentration_points(top)        # -10..+10
    verd_pts = _VERDICT_BONUS.get(verdict, 0.0)  # -40..+12

    reasons = [
        f"sécurité {sec}",
        f"liquidité ${liq:,.0f}",
        (f"holder max {top:.0f}%" if top is not None else "concentration inconnue"),
        f"verdict {verdict}",
    ]
    return RankedCandidate(
        contract=str(row.get("contract") or ""),
        symbol=str(row.get("symbol") or ""),
        rank_score=round(sec_pts + liq_pts + conc_pts + verd_pts, 1),
        security_score=sec,
        liquidity_usd=liq,
        top_holder_pct=top,
        verdict=verdict,
        reasons=reasons,
    )


def rank_candidates(rows: list[dict]) -> list[RankedCandidate]:
    """Ranks candidates from best to worst (descending composite score).

    Stable, deterministic tiebreak: score, then security, then liquidity.
    """
    ranked = [score_candidate(r) for r in rows if r]
    ranked.sort(
        key=lambda c: (c.rank_score, c.security_score, c.liquidity_usd),
        reverse=True,
    )
    return ranked


async def top_candidates(n: int = 10, *, lister=None) -> list[RankedCandidate]:
    """Reads the active pool and returns the N best ranked candidates.

    ``lister()`` (async) -> pool rows; default: active ``screened_pool.list_pool``.
    Injectable for offline tests.
    """
    if lister is None:
        from aria_core import screened_pool

        def lister():
            return screened_pool.list_pool(status="active", limit=1000)

    rows = await lister()
    return rank_candidates(rows)[: max(0, n)]


async def format_watchlist_report(n: int = 10, *, lister=None) -> str:
    """Text report of the watchlist pool -- extracted from _handle_watchlist
    (telegram_bot.py, 18/07, #213) so it's reusable by the natural-language
    router (`_nl_command_router.py`) WITHOUT duplicating the formatting --
    a single source of truth for "watchlist" regardless of the trigger
    (/watchlist or a question in French)."""
    tops = await top_candidates(n, lister=lister)
    if not tops:
        return "Pool de surveillance vide pour l'instant — aucun contrat suivi actuellement."

    lines = [f"👀 Contrats suivis de près ({len(tops)}/{n} demandés) :", ""]
    for i, c in enumerate(tops, start=1):
        name = c.symbol or f"{c.contract[:6]}…{c.contract[-4:]}"
        lines.append(
            f"{i}. {name} — score {c.rank_score:.0f} · sécurité {c.security_score} · "
            f"liq ${c.liquidity_usd:,.0f} · {c.verdict}"
        )
        lines.append(f"   {c.contract}")
    lines.append("")
    lines.append("Classement de priorité (jamais un ordre) — analyse complète : /vc <adresse>")
    return "\n".join(lines)


async def draw_top(n: int = 20, *, lister=None) -> list[dict]:
    """Alternative drawer for ``run_weekly_forecasts``: the N best ranked candidates
    instead of a random draw — to build the track record on the top of the pile.

    Returns pool rows (dicts) to stay compatible with the default drawer
    (``screened_pool.draw_lottery``). OPT-IN: must be passed explicitly, changes nothing
    to the existing behavior until it's used.
    """
    tops = await top_candidates(n, lister=lister)
    return [
        {
            "contract": c.contract,
            "symbol": c.symbol,
            "security_score": c.security_score,
            "liquidity_usd": c.liquidity_usd,
            "verdict": c.verdict,
            "rank_score": c.rank_score,
        }
        for c in tops
    ]
