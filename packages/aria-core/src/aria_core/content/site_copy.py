"""Public site copy — ARIA-owned marketing snippets."""

from __future__ import annotations

from aria_core.holding import DEFAULT_ARIA_TITLE, GOVERNANCE_RULE, holding_name
from aria_core.identity import ARIA_BIO, ARIA_DISPLAY_NAME, ARIA_HANDLE
from aria_core.narrative import one_liner
from aria_core.runtime import settings


def public_site_payload() -> dict:
    h = holding_name()
    return {
        "identity": ARIA_DISPLAY_NAME,
        "aria_title": DEFAULT_ARIA_TITLE,
        "holding": h,
        "holding_name": h,
        "holding_tagline": "Zero-Human Company holding — AI-operated parent entity",
        "governance_rule": GOVERNANCE_RULE,
        "one_liner": one_liner("en"),
        "bio_suggestion": ARIA_BIO,
        "x_handle": f"@{settings.aria_x_handle or ARIA_HANDLE}",
        "public_url": settings.public_site_url,
        "holding_domain": settings.holding_domain,
        "aria_role": (
            "ARIA is the heart of the project — she builds, markets, communicates, "
            "and maintains the FAQ for the holding and its subsidiaries."
        ),
        "pillars": [
            {
                "id": "build",
                "title": "Build",
                "body": "Engineering plans, sandbox experiments, product iteration, deploy coordination.",
            },
            {
                "id": "marketing",
                "title": "Marketing",
                "body": "Holding narrative, subsidiary positioning, launchpad strategy, milestone updates.",
            },
            {
                "id": "comms",
                "title": "Communication",
                "body": "Site copy, social drafts, and ZHC network messaging.",
            },
            {
                "id": "faq",
                "title": "FAQ & education",
                "body": "Structured answers, DEX education, holding transparency, public intelligence.",
            },
        ],
        "cta_primary": "Enter DEXPulse",
        "cta_secondary": "Ask ARIA",
    }