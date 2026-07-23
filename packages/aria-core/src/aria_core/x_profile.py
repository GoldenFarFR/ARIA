"""Public X profile @Aria_ZHC -- aligns name/bio/site with the Vanguard narrative.

Seam documented in `directives.md` ("X Profile"). The "location" field is
deliberately absent from the target: no canonical source in the repo for this
field -- an invented value is never synced (guardrail).

Two ways to trigger a sync:
- Admin Telegram command `/x profile sync` (authorization = the admin who types the
  command, no additional guard here -- same doctrine as the other
  admin commands in `telegram_bot.py`).
- Heartbeat task `x_profile_sync` (daily) -- gated by
  `x_profile_sync_enabled()` in `heartbeat.py`, since this is the only
  genuinely autonomous/outward-facing path (no human clicks).
"""
from __future__ import annotations

import os
from typing import Any

CANONICAL_FIELDS = ("name", "description", "url")


def x_profile_sync_enabled() -> bool:
    """Gate for the AUTOMATIC sync (heartbeat) only -- the admin Telegram
    command always remains available, authorization coming from the admin themselves."""
    return os.environ.get("ARIA_X_PROFILE_SYNC_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def canonical_x_profile() -> dict[str, str]:
    """Target fields, derived from the existing narrative (nothing new to write)."""
    from aria_core.identity import ARIA_DISPLAY_NAME
    from aria_core.narrative import holding_site_url, x_bio

    return {
        "name": ARIA_DISPLAY_NAME,
        "description": x_bio(),
        "url": holding_site_url(),
    }


def format_profile_summary(*, lang: str = "fr") -> str:
    target = canonical_x_profile()
    if lang == "en":
        return (
            f"Name: {target['name']}\n"
            f"Bio: {target['description']}\n"
            f"URL: {target['url']}"
        )
    return (
        f"Nom : {target['name']}\n"
        f"Bio : {target['description']}\n"
        f"Site : {target['url']}"
    )


async def fetch_live_x_profile() -> dict[str, str]:
    from aria_core.gateway.x_twitter import fetch_x_profile_fields

    return await fetch_x_profile_fields()


def profile_fields_differ(live: dict[str, str], target: dict[str, str]) -> list[str]:
    return [
        field
        for field in CANONICAL_FIELDS
        if (live.get(field) or "").strip() != (target.get(field) or "").strip()
    ]


async def sync_x_profile(*, force: bool = False) -> dict[str, Any]:
    """Compares the live profile to the target profile and applies if needed (or if `force`)."""
    from aria_core.gateway.x_twitter import apply_x_profile_fields, is_x_post_configured

    if not is_x_post_configured():
        return {"synced": False, "skipped": True, "reason": "x_not_configured"}

    target = canonical_x_profile()
    live = await fetch_live_x_profile()
    drift = profile_fields_differ(live, target)
    if not drift and not force:
        return {"synced": True, "drift": []}

    ok = await apply_x_profile_fields(target)
    if not ok:
        return {"synced": False, "error": "x_api_call_failed", "drift": drift}
    return {"synced": True, "drift": drift}
