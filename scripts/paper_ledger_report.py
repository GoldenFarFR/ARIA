"""CLI : instantané du registre paper-trading (thèse + winrate) pour une session VPS.

Toute la logique vit dans `aria_core.paper_ledger_report` (importée aussi par la
commande Telegram `/ledger`) -- ce fichier ne fait que l'appeler et imprimer.
Usage : `docker exec aria-api python scripts/paper_ledger_report.py`.
"""
from __future__ import annotations

import asyncio
import json

from aria_core.paper_ledger_report import build_report


async def main() -> None:
    text, machine = await build_report()
    print(text)
    print()
    print("--- JSON (parsing automatique) ---")
    print(json.dumps(machine, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
