"""Discovery adapter registry — one single entry point per Base launchpad.

Three categories, distinguished by the LIQUIDITY MODEL (not by the launchpad
itself):

- **bonding**: the token first lives on a bonding curve, WITHOUT a DEX pair,
  before "graduating" (e.g. Virtuals). These candidates join the dedicated
  15% niche (``skills/bonding_absorber.py`` -> ``screened_pool`` under
  ``network="base-bonding"``), because the standard safety filter
  (``safety_screen``) requires DEX liquidity and would ALWAYS wrongly reject
  a token still bonding.
- **direct**: real DEX liquidity from deployment (Clanker, Flaunch, Zora, and
  a Virtuals token that just graduated). These candidates join the existing
  STANDARD pipeline (``token_absorber.absorb``, 85% VC pool) — nothing
  special to do, just a faster discovery point than waiting for them to show
  up in ``discover_top_pools``.
- **unknown**: launchpad identified (operator/research) but diligence NOT
  yet done (no client, no confirmed address) — documented seam,
  ``discover=None``. NEVER build a client on a hypothesis ("depth
  proportional to the stakes" doctrine — see CLAUDE.md): Bankr/Ape.store/
  Mint.club wait for real research before getting a real
  ``services/<x>.py`` client.

The "bonding vs direct" classification reuses ``knowledge/launchpads.yaml``
(``mint_authority.is_bonding_launchpad``) — the SOLE source of truth, never
duplicated here.

No on-chain writes, no decisions: discovery only (contract addresses).
Absorption (screen + pool decision) lives in the calling modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Discoverer = Callable[..., Awaitable[list[str]]]


@dataclass(frozen=True)
class LaunchpadAdapter:
    """A registry entry: a launchpad, its category, its discoverer (or None)."""

    key: str
    label: str
    category: str  # "bonding" | "direct" | "unknown"
    discover: Discoverer | None  # None = empty seam, diligence not yet done


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
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("launchpad_discovery: clanker fetch_recent failed (%s)", exc)
        return []
    seen: dict[str, None] = {}
    for token in tokens:
        addr = (token.contract_address or "").lower()
        if addr.startswith("0x") and len(addr) == 42 and addr not in seen:
            seen[addr] = None
        if len(seen) >= limit:
            break
    return list(seen.keys())


# Registry: ONE entry per recognized launchpad. Seams (discover=None) document
# an intent without building a client — see the module docstring.
_ADAPTERS: dict[str, LaunchpadAdapter] = {
    "virtuals_bonding": LaunchpadAdapter(
        "virtuals_bonding", "Virtuals Protocol (bonding)", "bonding", _discover_virtuals_bonding
    ),
    "virtuals_graduated": LaunchpadAdapter(
        "virtuals_graduated", "Virtuals Protocol (graduated)", "direct", _discover_virtuals_graduated
    ),
    "clanker": LaunchpadAdapter("clanker", "Clanker", "direct", _discover_clanker_direct),
    "flaunch": LaunchpadAdapter("flaunch", "Flaunch", "direct", None),
    "zora": LaunchpadAdapter("zora", "Zora", "direct", None),
    "bankr": LaunchpadAdapter("bankr", "Bankr", "unknown", None),
    "ape_store": LaunchpadAdapter("ape_store", "Ape.store", "unknown", None),
    "mint_club": LaunchpadAdapter("mint_club", "Mint.club", "unknown", None),
}


def list_adapters(*, category: str | None = None) -> list[LaunchpadAdapter]:
    """The registry (copy), filterable by category. Stable (insertion) order."""
    values = list(_ADAPTERS.values())
    if category is None:
        return values
    return [a for a in values if a.category == category]


async def _run_active_adapters(category: str, *, limit_per_launchpad: int) -> dict[str, list[str]]:
    """Runs every ``category`` adapter that has a real discoverer (best-effort).

    A failing adapter never blocks the others (result ``[]`` for it).
    """
    out: dict[str, list[str]] = {}
    for adapter in list_adapters(category=category):
        if adapter.discover is None:
            continue
        try:
            out[adapter.key] = await adapter.discover(limit=limit_per_launchpad)
        except Exception as exc:  # noqa: BLE001 — a failing launchpad doesn't stop the others
            logger.info("launchpad_discovery: %s failed (%s)", adapter.key, exc)
            out[adapter.key] = []
    return out


async def discover_bonding_candidates(*, limit_per_launchpad: int = 50) -> dict[str, list[str]]:
    """``{launchpad_key: [addresses]}`` — candidates still bonding (15% niche)."""
    return await _run_active_adapters("bonding", limit_per_launchpad=limit_per_launchpad)


async def discover_direct_candidates(*, limit_per_launchpad: int = 50) -> dict[str, list[str]]:
    """``{launchpad_key: [addresses]}`` — candidates with real DEX liquidity (85% VC pool)."""
    return await _run_active_adapters("direct", limit_per_launchpad=limit_per_launchpad)
