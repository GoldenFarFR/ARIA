"""Profil public X @Aria_ZHC -- aligne nom/bio/site sur la narrative Vanguard.

Seam documenté dans `directives.md` (« Profil X »). Champ « lieu » volontairement
absent de la cible : aucune source canonique dans le repo pour ce champ -- on ne
synchronise pas une donnée inventée (dôme).

Deux façons de déclencher une sync :
- Commande Telegram admin `/x profile sync` (autorisation = l'admin qui tape la
  commande, pas de garde supplémentaire ici -- même doctrine que les autres
  commandes admin de `telegram_bot.py`).
- Tâche heartbeat `x_profile_sync` (quotidienne) -- gardée par
  `x_profile_sync_enabled()` dans `heartbeat.py`, car c'est le seul chemin
  réellement autonome/outward-facing (aucun humain ne clique).
"""
from __future__ import annotations

import os
from typing import Any

CANONICAL_FIELDS = ("name", "description", "url")


def x_profile_sync_enabled() -> bool:
    """Gate de la sync AUTOMATIQUE (heartbeat) uniquement -- la commande Telegram
    admin reste toujours disponible, l'autorisation venant de l'admin lui-même."""
    return os.environ.get("ARIA_X_PROFILE_SYNC_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def canonical_x_profile() -> dict[str, str]:
    """Champs cibles, dérivés de la narrative existante (rien de nouveau à rédiger)."""
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
    """Compare le profil live au profil cible et applique si nécessaire (ou si `force`)."""
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
