"""Classement des candidats du pool screené — le « tri » de l'analyse de masse.

Le crawler absorbe en continu des milliers de contrats ; le filtre de sécurité garde
les valables dans ``screened_pool``. Ce module TRIE ces gardés par un score composite
TRANSPARENT (chaque point s'explique) pour faire remonter les meilleurs candidats vers
l'analyse VC approfondie et le track-record.

Ce n'est PAS une décision d'achat : un classement de priorité, jamais un ordre. Pur et
déterministe (mêmes lignes -> même classement), aucun appel réseau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log10

# Pondérations transparentes et bornées. La sécurité domine (0..100) ; la liquidité,
# la concentration et le verdict ajustent à la marge. Documentées et ajustables.
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
    """Liquidité en points (log-échelle, saturée) : 30k -> 0, ~100k -> +6, ~1M -> +17, plafond 25."""
    if liq <= 30_000:
        return 0.0
    return min(25.0, 11.0 * log10(liq / 30_000.0))


def _concentration_points(top_holder_pct: float | None) -> float:
    """Moins un holder domine, mieux c'est. None (inconnu) = neutre. Borné -10..+10."""
    if top_holder_pct is None:
        return 0.0
    if top_holder_pct <= 10:
        return 10.0
    if top_holder_pct >= 30:
        return -10.0
    return 10.0 - (top_holder_pct - 10.0)  # linéaire : 10% -> +10, 30% -> -10


def score_candidate(row: dict) -> RankedCandidate:
    """Score composite d'un candidat (une ligne de screened_pool). Transparent et borné."""
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
    """Classe les candidats du meilleur au moins bon (score composite décroissant).

    Départage stable et déterministe : score, puis sécurité, puis liquidité.
    """
    ranked = [score_candidate(r) for r in rows if r]
    ranked.sort(
        key=lambda c: (c.rank_score, c.security_score, c.liquidity_usd),
        reverse=True,
    )
    return ranked


async def top_candidates(n: int = 10, *, lister=None) -> list[RankedCandidate]:
    """Lit le pool actif et renvoie les N meilleurs candidats classés.

    ``lister()`` (async) -> lignes du pool ; défaut : ``screened_pool.list_pool`` actif.
    Injectable pour les tests hors-ligne.
    """
    if lister is None:
        from aria_core import screened_pool

        def lister():
            return screened_pool.list_pool(status="active", limit=1000)

    rows = await lister()
    return rank_candidates(rows)[: max(0, n)]


async def format_watchlist_report(n: int = 10, *, lister=None) -> str:
    """Rapport texte du pool de surveillance -- extrait de _handle_watchlist
    (telegram_bot.py, 18/07, #213) pour être réutilisable par le routeur
    langage-naturel (`_nl_command_router.py`) SANS dupliquer le formatage --
    une seule source de vérité pour "watchlist" quel que soit le déclencheur
    (/watchlist ou une question en français)."""
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
    """Drawer alternatif pour ``run_weekly_forecasts`` : les N meilleurs candidats classés
    au lieu d'un tirage aléatoire — pour bâtir le track-record sur le haut du panier.

    Renvoie des lignes de pool (dicts) pour rester compatible avec le drawer par défaut
    (``screened_pool.draw_lottery``). OPT-IN : à passer explicitement, ne change rien au
    comportement existant tant qu'on ne l'utilise pas.
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
