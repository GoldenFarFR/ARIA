"""ROI projection via historical comparables — Vault 3 (facts-only, deterministic).

Places a token within its **sector's history**: "at the market cap of a
sector's median comparable, this token would be worth Nx". The goal is to
make an order of magnitude **tangible**, not to promise anything.

RED LINE (dome): this is **never** a target, a forecast, or a return
promise. A past comparable doesn't repeat itself. The engine only does
**arithmetic** (reference market cap / current market cap = multiple) over
editable milestones (`knowledge/roi_comparables.yaml`). It never invents a
figure: with no known current market cap, ``available=False`` (the report
simply omits the section). Same inputs -> same output.
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
# A "comparable" smaller than the current market cap is never shown (it
# wouldn't be a projection). An absurd multiple (> this cap) isn't shown
# either: beyond it, the order of magnitude no longer serves its educational
# purpose and would flirt with a promise. An honesty filter, not a dogma.
_MAX_REASONABLE_MULTIPLE = 500.0


@dataclass(frozen=True)
class ComparableScenario:
    """A placement: "at this reference market cap, this token would do Nx"."""

    label: str
    ref_mcap_usd: float
    multiple: float
    note: str = ""


@dataclass(frozen=True)
class ROIComparablesResult:
    """ROI projection (or its absence), with its factual basis and its disclaimer."""

    available: bool
    current_mcap_usd: float | None = None
    basis: str = "market_cap"  # 'market_cap' or 'fdv' (fallback if mcap unavailable)
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
    """Alias -> canonical sector key table (built from the YAML)."""
    idx: dict[str, str] = {}
    for key, block in (_cfg().get("sectors") or {}).items():
        idx[key.lower()] = key
        for alias in (block or {}).get("aliases", []) or []:
            idx[str(alias).lower()] = key
    return idx


def resolve_sector(categories: list[str] | None) -> tuple[str, bool]:
    """Maps categories (e.g. CoinGecko) to a known sector key.

    Returns (sector_key, recognized). ``recognized=False`` → falls back to
    ``generic``, and the report flags it (no false precision).
    """
    idx = _sector_index()
    for cat in categories or []:
        norm = str(cat).strip().lower().replace(" ", "-")
        if norm in idx:
            return idx[norm], True
        # Keyword match (e.g. does "AI Agents" contain "ai-agents"? no,
        # but "ai" does): each alias is tested as a substring.
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
    """Projects a token's placement within its sector's history.

    ``current_mcap_usd``: real current market cap (market cap preferred,
    otherwise FDV with ``basis='fdv'``). ``categories``: the token's
    sector(s) (CoinGecko). With no valid market cap → ``available=False``.
    Only shows UPWARD placements (comparable > current) of a reasonable
    order of magnitude.
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
            continue  # not an upward projection -> not displayed
        multiple = ref / current_mcap_usd
        if multiple > _MAX_REASONABLE_MULTIPLE:
            continue  # non-educational order of magnitude -> not displayed
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
