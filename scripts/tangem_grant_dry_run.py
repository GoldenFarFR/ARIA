"""CLI : dry-run du grant Spend Permission via Tangem (lecture on-chain seule,
aucun tap, aucune transaction). Usage :
`docker exec aria-api python scripts/tangem_grant_dry_run.py`
"""
from __future__ import annotations

import asyncio
import json


async def main() -> None:
    from aria_core.agent_wallet_smart_swing_grant import grant_spend_permission_via_tangem

    result = await grant_spend_permission_via_tangem(dry_run=True)
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
