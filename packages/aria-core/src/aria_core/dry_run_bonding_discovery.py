"""Dry-run manuel de la découverte multi-launchpad (bonding + gradués) -- AVANT
d'activer `ARIA_BONDING_DISCOVERY_ENABLED` en continu sur le heartbeat.

N'écrit QUE dans le pool screené local (`screened_pool`, network="base-bonding"
ou "base" selon le volet) -- même effet de bord que le cycle heartbeat réel,
mais déclenché une fois, à la main, pour lire le résultat avant d'armer le gate.

Usage (sur le VPS, réseau dispo) :
    docker exec aria-api python -m aria_core.dry_run_bonding_discovery
"""
from __future__ import annotations

import asyncio

from aria_core.simulate_lifecycle import _configure_host


def _line(txt: str = "") -> None:
    print(txt, flush=True)


async def dry_run() -> dict:
    from aria_core.skills.bonding_absorber import run_bonding_discovery_cycle

    _line("=" * 64)
    _line("DRY-RUN DECOUVERTE MULTI-LAUNCHPAD (bonding + gradues)")
    _line("=" * 64)

    configured = _configure_host()
    _line(f"\n[*] Config hote : {'OK' if configured else 'echouee (voir log ci-dessus)'}")

    _line("\n[1] Cycle de decouverte (chaque launchpad best-effort, un echec n'efface pas les autres)...")
    result = await run_bonding_discovery_cycle()

    _line("\n[2] RESULTAT")
    _line("-" * 64)
    for key, value in result.items():
        _line(f"    {key} = {value}")
    _line("=" * 64)
    _line("DRY-RUN TERMINE — verifie le resultat avant d'activer ARIA_BONDING_DISCOVERY_ENABLED")
    return result


def main() -> None:
    asyncio.run(dry_run())


if __name__ == "__main__":
    main()
