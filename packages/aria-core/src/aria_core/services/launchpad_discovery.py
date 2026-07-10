"""Registre des adaptateurs de découverte — un point d'entrée unique par launchpad Base.

Trois catégories, distinguées par le MODÈLE DE LIQUIDITÉ (pas par le launchpad
lui-même) :

- **bonding** : le token vit d'abord sur une courbe de bonding, SANS paire DEX, avant
  de « graduer » (ex. Virtuals). Ces candidats rejoignent la niche 15% dédiée
  (``skills/bonding_absorber.py`` → ``screened_pool`` sous ``network="base-bonding"``),
  car le filtre de sécurité standard (``safety_screen``) exige une liquidité DEX et
  rejetterait TOUJOURS à tort un token encore en bonding.
- **direct** : liquidité DEX réelle dès le déploiement (Clanker, Flaunch, Zora, et un
  token Virtuals qui vient de graduer). Ces candidats rejoignent le pipeline STANDARD
  existant (``token_absorber.absorb``, pool 85% VC) — rien de spécial à faire, juste
  un point de découverte plus rapide que d'attendre leur apparition dans
  ``discover_top_pools``.
- **unknown** : launchpad identifié (opérateur/recherche) mais diligence PAS encore
  faite (pas de client, pas d'adresse confirmée) — seam documenté, ``discover=None``.
  Ne JAMAIS fabriquer un client sur une hypothèse (doctrine « profondeur
  proportionnelle à l'enjeu » — cf. CLAUDE.md) : Bankr/Ape.store/Mint.club attendent
  une vraie recherche avant d'obtenir un vrai client ``services/<x>.py``.

La classification « bonding vs direct » réutilise ``knowledge/launchpads.yaml``
(``mint_authority.is_bonding_launchpad``) — SEULE source de vérité, jamais dupliquée
ici.

Aucune écriture on-chain, aucune décision : uniquement de la découverte (adresses de
contrat). L'absorption (screen + décision pool) vit dans les modules appelants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Discoverer = Callable[..., Awaitable[list[str]]]


@dataclass(frozen=True)
class LaunchpadAdapter:
    """Une entrée du registre : un launchpad, sa catégorie, son découvreur (ou None)."""

    key: str
    label: str
    category: str  # "bonding" | "direct" | "unknown"
    discover: Discoverer | None  # None = seam vide, diligence pas encore faite


async def _discover_virtuals_bonding(*, limit: int = 50) -> list[str]:
    from aria_core.base_crawler import discover_virtuals_tokens

    return await discover_virtuals_tokens(limit=limit)


async def _discover_virtuals_graduated(*, limit: int = 50) -> list[str]:
    from aria_core.base_crawler import discover_virtuals_graduated_tokens

    return await discover_virtuals_graduated_tokens(limit=limit)


async def _discover_clanker_direct(*, limit: int = 50) -> list[str]:
    from aria_core.services.clanker import clanker_client

    try:
        tokens = await clanker_client.fetch_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("launchpad_discovery: clanker fetch_recent échoué (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for token in tokens:
        addr = (token.contract_address or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


# Registre : UNE entrée par launchpad reconnu. Les seams (discover=None) documentent
# une intention sans fabriquer de client — cf. docstring du module.
_ADAPTERS: dict[str, LaunchpadAdapter] = {
    "virtuals_bonding": LaunchpadAdapter(
        "virtuals_bonding", "Virtuals Protocol (bonding)", "bonding", _discover_virtuals_bonding
    ),
    "virtuals_graduated": LaunchpadAdapter(
        "virtuals_graduated", "Virtuals Protocol (gradué)", "direct", _discover_virtuals_graduated
    ),
    "clanker": LaunchpadAdapter("clanker", "Clanker", "direct", _discover_clanker_direct),
    "flaunch": LaunchpadAdapter("flaunch", "Flaunch", "direct", None),
    "zora": LaunchpadAdapter("zora", "Zora", "direct", None),
    "bankr": LaunchpadAdapter("bankr", "Bankr", "unknown", None),
    "ape_store": LaunchpadAdapter("ape_store", "Ape.store", "unknown", None),
    "mint_club": LaunchpadAdapter("mint_club", "Mint.club", "unknown", None),
}


def list_adapters(*, category: str | None = None) -> list[LaunchpadAdapter]:
    """Le registre (copie), filtrable par catégorie. Ordre stable (insertion)."""
    values = list(_ADAPTERS.values())
    if category is None:
        return values
    return [a for a in values if a.category == category]


async def _run_active_adapters(category: str, *, limit_per_launchpad: int) -> dict[str, list[str]]:
    """Exécute tous les adaptateurs ``category`` ayant un vrai découvreur (best-effort).

    Un adaptateur qui échoue ne bloque jamais les autres (résultat ``[]`` pour lui).
    """
    out: dict[str, list[str]] = {}
    for adapter in list_adapters(category=category):
        if adapter.discover is None:
            continue
        try:
            out[adapter.key] = await adapter.discover(limit=limit_per_launchpad)
        except Exception as exc:  # noqa: BLE001 — un launchpad en panne n'arrête pas les autres
            logger.info("launchpad_discovery: %s échec (%s)", adapter.key, exc)
            out[adapter.key] = []
    return out


async def discover_bonding_candidates(*, limit_per_launchpad: int = 50) -> dict[str, list[str]]:
    """``{launchpad_key: [adresses]}`` — candidats encore en bonding (niche 15%)."""
    return await _run_active_adapters("bonding", limit_per_launchpad=limit_per_launchpad)


async def discover_direct_candidates(*, limit_per_launchpad: int = 50) -> dict[str, list[str]]:
    """``{launchpad_key: [adresses]}`` — candidats à liquidité DEX réelle (pool 85% VC)."""
    return await _run_active_adapters("direct", limit_per_launchpad=limit_per_launchpad)
