#!/usr/bin/env python3
"""Ouvrier Cursor — juge QI ARIA sur métriques réelles (GitHub, health, gaps)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> int:
    from aria_core import bootstrap
    from app.config import settings
    from app.paths import data_dir

    bootstrap.configure(data_dir=data_dir(), settings=settings)
    from aria_core.qi_auto_judge import JUDGE_OUVRIER, format_judge_report, run_qi_auto_judge

    result = await run_qi_auto_judge(source=JUDGE_OUVRIER, lang="fr")
    print(result.get("message") or format_judge_report(result, lang="fr"))
    events = result.get("events") or []
    print(f"\n{len(events)} palier(s) appliqué(s). Indice {result.get('global_index')}/1000")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))