"""Manual dry-run of multi-launchpad discovery (bonding + graduated) -- BEFORE
enabling `ARIA_BONDING_DISCOVERY_ENABLED` continuously on the heartbeat.

Writes ONLY to the local screened pool (`screened_pool`, network="base-bonding"
or "base" depending on the track) -- same side effect as the real heartbeat
cycle, but triggered once, by hand, to read the result before arming the gate.

Usage (on the VPS, network available):
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
    _line("DRY-RUN MULTI-LAUNCHPAD DISCOVERY (bonding + graduated)")
    _line("=" * 64)

    configured = _configure_host()
    _line(f"\n[*] Host config: {'OK' if configured else 'failed (see log above)'}")

    _line("\n[1] Discovery cycle (each launchpad best-effort, one failure doesn't erase the others)...")
    result = await run_bonding_discovery_cycle()

    _line("\n[2] RESULT")
    _line("-" * 64)
    for key, value in result.items():
        _line(f"    {key} = {value}")
    _line("=" * 64)
    _line("DRY-RUN DONE — check the result before enabling ARIA_BONDING_DISCOVERY_ENABLED")
    return result


def main() -> None:
    asyncio.run(dry_run())


if __name__ == "__main__":
    main()
