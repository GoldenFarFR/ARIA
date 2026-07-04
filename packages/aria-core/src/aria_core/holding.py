"""Aria Vanguard ZHC — holding structure for all ARIA ventures."""

from __future__ import annotations

from dataclasses import dataclass

from aria_core.runtime import settings

HOLDING_SLUG = "aria-vanguard-zhc"
DEXPULSE_SLUG = "dexpulse"  # retired 2026-06-19 — kept for repertoire migration only
ARIA_MARKET_SLUG = "aria-market"
FLAGSHIP_PRODUCT = "Aria Market"
DEFAULT_HOLDING_DOMAIN = "ariavanguardzhc.com"

DEFAULT_HOLDING_NAME = "Aria Vanguard ZHC"
DEFAULT_HOLDING_TAGLINE = "Zero-Human Company holding — parent entity for autonomous ventures"
DEFAULT_ARIA_TITLE = "Chief Autonomous Officer (CAO)"
GOVERNANCE_RULE = (
    "Every ARIA venture is a subsidiary of Aria Vanguard ZHC. "
    "Aria Market is the flagship subsidiary — not the holding. "
    "DEXPulse was retired 2026-06-19 (repo deleted). "
    "All current and future projects register under the holding."
)
SUBSIDIARY_OF_LABEL = "Subsidiary of Aria Vanguard ZHC"


@dataclass(frozen=True)
class SubsidiaryTemplate:
    slug: str
    name: str
    description: str
    status: str
    category: str
    priority: int
    tags: tuple[str, ...]
    zhc_aligned: bool


HOLDING_TEMPLATE = SubsidiaryTemplate(
    slug=HOLDING_SLUG,
    name=DEFAULT_HOLDING_NAME,
    description=(
        "AI-operated ZHC holding company. Owns and coordinates portfolio ventures. "
        "ARIA serves as Chief Autonomous Officer."
    ),
    status="live",
    category="holding",
    priority=5,
    tags=("zhc", "holding", "parent"),
    zhc_aligned=True,
)

DEFAULT_SUBSIDIARIES: tuple[SubsidiaryTemplate, ...] = (
    SubsidiaryTemplate(
        slug=ARIA_MARKET_SLUG,
        name="Aria Market",
        description=(
            "Subsidiary of Aria Vanguard ZHC. Watchlist-first DEX analyzer — flagship product. "
            "Multi-timeframe signals, alerts, DexScreener embeds, ARIA agent."
        ),
        status="live",
        category="product",
        priority=5,
        tags=("dex", "saas", "flagship", "aria-market"),
        zhc_aligned=True,
    ),
    SubsidiaryTemplate(
        slug=DEXPULSE_SLUG,
        name="DEXPulse",
        description=(
            "RETIRED 2026-06-19. Former flagship DEX analyzer — repo and Render service deleted. "
            "Successor: Aria Market in aria-vanguard."
        ),
        status="retired",
        category="product",
        priority=1,
        tags=("dex", "retired", "deprecated"),
        zhc_aligned=False,
    ),
)


def holding_name() -> str:
    return (settings.aria_holding_name or DEFAULT_HOLDING_NAME).strip()


def holding_structure_text() -> str:
    subs = ", ".join(s.name for s in DEFAULT_SUBSIDIARIES)
    return (
        f"{holding_name()} (holding) owns and coordinates portfolio ventures. "
        f"Current subsidiaries: {subs}. "
        f"New projects are registered as subsidiaries of the holding."
    )


def aria_org_prompt() -> str:
    return (
        f"ORGANISATION : {holding_name()} est la holding mère (ZHC). "
        f"ARIA ZHC en est la {DEFAULT_ARIA_TITLE} — elle opère la holding et ses filiales. "
        f"Aria Market est la filiale produit phare (DEXPulse retiré 2026-06-19). "
        f"Tout nouveau projet doit être rattaché à la holding dans le répertoire."
    )