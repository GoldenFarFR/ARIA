"""Projection ROI par comparables historiques — Voûte 3 (facts-only, déterministe).

Replace un token dans l'**histoire de son secteur** : « à la capitalisation d'un
comparable médian du secteur, ce token vaudrait Nx ». Le but est de rendre un ordre
de grandeur **tangible**, pas de promettre quoi que ce soit.

LIGNE ROUGE (dôme) : ce n'est **jamais** une cible, une prévision, ni une promesse
de rendement. Un comparable du passé ne se reproduit pas. Le moteur ne fait qu'une
**arithmétique** (capitalisation de référence / capitalisation actuelle = multiple)
sur des jalons éditables (`knowledge/roi_comparables.yaml`). Il n'invente aucun
chiffre : sans capitalisation actuelle connue, ``available=False`` (le rapport omet
simplement la section). Mêmes entrées → même sortie.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_COMPARABLES_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "roi_comparables.yaml"

_DEFAULT_DISCLAIMER = (
    "Placement historique par comparables, pas une prevision ni une cible. "
    "Aucune garantie de rendement."
)
# On ne montre pas un « comparable » plus petit que la capitalisation actuelle
# (ce ne serait pas une projection). On ne montre pas non plus un multiple
# absurde (> ce plafond) : au-delà, l'ordre de grandeur n'a plus de sens
# pédagogique et flirterait avec la promesse. Filtre honnêteté, pas dogme.
_MAX_REASONABLE_MULTIPLE = 500.0


@dataclass(frozen=True)
class ComparableScenario:
    """Un placement : « à cette capitalisation de référence, ce token ferait Nx »."""

    label: str
    ref_mcap_usd: float
    multiple: float
    note: str = ""


@dataclass(frozen=True)
class ROIComparablesResult:
    """Projection ROI (ou son absence), avec sa base factuelle et son avertissement."""

    available: bool
    current_mcap_usd: float | None = None
    basis: str = "market_cap"  # 'market_cap' ou 'fdv' (repli si mcap indisponible)
    sector: str | None = None
    sector_recognized: bool = False
    scenarios: list[ComparableScenario] = field(default_factory=list)
    disclaimer: str = _DEFAULT_DISCLAIMER
    reason: str = ""


@lru_cache(maxsize=1)
def _cfg() -> dict[str, Any]:
    if not _COMPARABLES_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_COMPARABLES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _sector_index() -> dict[str, str]:
    """Table alias → clé de secteur canonique (construite depuis le YAML)."""
    idx: dict[str, str] = {}
    for key, block in (_cfg().get("sectors") or {}).items():
        idx[key.lower()] = key
        for alias in (block or {}).get("aliases", []) or []:
            idx[str(alias).lower()] = key
    return idx


def resolve_sector(categories: list[str] | None) -> tuple[str, bool]:
    """Mappe des catégories (ex. CoinGecko) vers une clé de secteur connue.

    Retourne (clé_secteur, reconnu). ``reconnu=False`` → repli sur ``generic``,
    et le rapport le signale (pas de fausse précision).
    """
    idx = _sector_index()
    for cat in categories or []:
        norm = str(cat).strip().lower().replace(" ", "-")
        if norm in idx:
            return idx[norm], True
        # Correspondance par mot-clé (ex. "AI Agents" contient "ai-agents" ? non,
        # mais "ai" oui) : on teste chaque alias comme sous-chaîne.
        for alias, key in idx.items():
            if alias and alias in norm:
                return key, True
    return "generic", False


def project_roi(
    current_mcap_usd: float | None,
    categories: list[str] | None = None,
    *,
    basis: str = "market_cap",
    max_scenarios: int = 3,
) -> ROIComparablesResult:
    """Projette le placement d'un token dans l'histoire de son secteur.

    ``current_mcap_usd`` : capitalisation actuelle réelle (market cap de préférence,
    sinon FDV avec ``basis='fdv'``). ``categories`` : secteur(s) du token (CoinGecko).
    Sans capitalisation valide → ``available=False``. Ne montre que les placements
    à la HAUSSE (comparable > actuel) et d'un ordre de grandeur raisonnable.
    """
    disclaimer = str(_cfg().get("disclaimer") or _DEFAULT_DISCLAIMER).strip()

    if not current_mcap_usd or current_mcap_usd <= 0:
        return ROIComparablesResult(
            available=False,
            basis=basis,
            disclaimer=disclaimer,
            reason="capitalisation actuelle indisponible",
        )

    sector, recognized = resolve_sector(categories)
    block = (_cfg().get("sectors") or {}).get(sector) or {}
    milestones = block.get("milestones") or []

    scenarios: list[ComparableScenario] = []
    for m in milestones:
        try:
            ref = float(m.get("ref_mcap_usd"))
        except (TypeError, ValueError):
            continue
        if ref <= current_mcap_usd:
            continue  # pas une projection à la hausse → on n'affiche pas
        multiple = ref / current_mcap_usd
        if multiple > _MAX_REASONABLE_MULTIPLE:
            continue  # ordre de grandeur non pédagogique → on n'affiche pas
        scenarios.append(
            ComparableScenario(
                label=str(m.get("label") or "comparable"),
                ref_mcap_usd=ref,
                multiple=round(multiple, 1),
                note=str(m.get("note") or ""),
            )
        )

    scenarios.sort(key=lambda s: s.ref_mcap_usd)
    scenarios = scenarios[:max_scenarios]

    if not scenarios:
        return ROIComparablesResult(
            available=False,
            current_mcap_usd=current_mcap_usd,
            basis=basis,
            sector=sector,
            sector_recognized=recognized,
            disclaimer=disclaimer,
            reason="aucun comparable a la hausse dans un ordre de grandeur raisonnable",
        )

    return ROIComparablesResult(
        available=True,
        current_mcap_usd=current_mcap_usd,
        basis=basis,
        sector=sector,
        sector_recognized=recognized,
        scenarios=scenarios,
        disclaimer=disclaimer,
    )
