"""CLI : grant RÉEL de la Spend Permission via Tangem (dry_run=False).
Génère un lien WalletConnect à ouvrir dans l'app Tangem, attend le tap de
l'opérateur, puis vérifie la confirmation on-chain. Usage :
`docker exec aria-api python scripts/tangem_grant_real.py`
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")


async def main() -> None:
    from aria_core.agent_wallet_smart_swing_grant import grant_spend_permission_via_tangem

    result = await grant_spend_permission_via_tangem(dry_run=False)
    print("\n=== RESULTAT FINAL ===")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
