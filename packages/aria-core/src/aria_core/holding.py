"""Aria Vanguard ZHC — holding structure for all ARIA ventures."""

from __future__ import annotations

from dataclasses import dataclass

from aria_core.runtime import settings

HOLDING_SLUG = "aria-vanguard-zhc"
DEXPULSE_SLUG = "dexpulse"  # retired 2026-06-19 — kept so repertoire cleanup can find/purge it
ARIA_MARKET_SLUG = "aria-market"  # retired codename — kept so repertoire cleanup can find/purge it
FLAGSHIP_PRODUCT = "Aria Market"  # retired codename — still referenced by narrative/grounding copy pending a dedicated cleanup
DEFAULT_HOLDING_DOMAIN = "ariavanguardzhc.com"

DEFAULT_HOLDING_NAME = "Aria Vanguard ZHC"
DEFAULT_HOLDING_TAGLINE = "Zero-Human Company holding — parent entity for autonomous ventures"
DEFAULT_ARIA_TITLE = "Chief Autonomous Officer (CAO)"
GOVERNANCE_RULE = (
    "Every ARIA venture registers as a subsidiary of Aria Vanguard ZHC — never the holding itself. "
    "No subsidiary is currently live: ARIA operates the holding directly. "
    "DEXPulse and Aria Market are retired codenames, no longer live products."
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

# No subsidiary is currently live — Aria Market and DEXPulse are retired codenames.
# New ventures register here as they launch (seam kept ready, see docs/architecture-extensibilite.md).
DEFAULT_SUBSIDIARIES: tuple[SubsidiaryTemplate, ...] = ()


def holding_name() -> str:
    return (settings.aria_holding_name or DEFAULT_HOLDING_NAME).strip()


def holding_structure_text() -> str:
    if not DEFAULT_SUBSIDIARIES:
        return (
            f"{holding_name()} (holding) owns and coordinates portfolio ventures. "
            f"No subsidiary is currently live — ARIA operates the holding directly. "
            f"New projects are registered as subsidiaries of the holding."
        )
    subs = ", ".join(s.name for s in DEFAULT_SUBSIDIARIES)
    return (
        f"{holding_name()} (holding) owns and coordinates portfolio ventures. "
        f"Current subsidiaries: {subs}. "
        f"New projects are registered as subsidiaries of the holding."
    )


def aria_org_prompt() -> str:
    return (
        f"ORGANISATION : {holding_name()} est la holding mère (ZHC). "
        f"ARIA ZHC en est la {DEFAULT_ARIA_TITLE} — elle opère la holding directement, "
        f"aucune filiale n'est actuellement live. "
        f"Tout nouveau projet doit être rattaché à la holding dans le répertoire."
    )