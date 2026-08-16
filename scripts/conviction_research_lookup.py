"""CLI : historique de diligence conviction_research pour un contrat.

Diagnostic tool for future Claude Code sessions -- checks the vector-memory
flag explicitly before searching, so a session never mistakes "memory is
disabled" for "nothing was ever found" (a real confusion hit while
investigating an x402 twit.sh payment on 2026-07-25 -- `is_available()`
silently returns False when `ARIA_VECTOR_MEMORY_ENABLED` is off, and both
`lancedb_store.store()`/`search()` then no-op without any error).

All logic reused from `aria_core.conviction_research`/`aria_core.memory.vector`
-- this file only calls and prints.
Usage : `docker exec aria-api python scripts/conviction_research_lookup.py <contract> [--chain base]`
"""
from __future__ import annotations

import argparse
import asyncio
import json


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", help="token contract address")
    parser.add_argument("--chain", default="base")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    from aria_core.memory.vector import lancedb_store

    if not lancedb_store.is_available():
        status = lancedb_store.vector_store_status()
        print("VECTOR MEMORY UNAVAILABLE -- nothing can be retrieved, regardless of what")
        print("conviction_research actually computed at the time (it silently no-ops the")
        print("store() call when this is off, no error/log).")
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return

    from aria_core.conviction_research import get_research_history

    history = await get_research_history(args.contract, args.chain, limit=args.limit)
    if not history:
        print(f"No stored conviction_research entry for {args.contract} ({args.chain}).")
        return

    for entry in history:
        print("---")
        print(json.dumps(entry.__dict__, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
