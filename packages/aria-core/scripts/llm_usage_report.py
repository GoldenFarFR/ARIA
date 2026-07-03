"""Rapport consommation tokens LLM — data/llm-usage/YYYY-MM.jsonl"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _repo_data_dir() -> Path:
    raw = os.environ.get("DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    repo = os.environ.get("ARIA_REPO_ROOT", "").strip()
    if repo:
        return Path(repo) / "vanguard" / "backend" / "data"
    return Path.cwd() / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="Rapport tokens LLM ARIA")
    parser.add_argument("--month", help="YYYY-MM (défaut: mois UTC courant)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Une ligne dashboard KART Grok Build (xAI)",
    )
    parser.add_argument(
        "--grok",
        action="store_true",
        help="Alias --compact (Grok Build / xAI uniquement)",
    )
    parser.add_argument(
        "--cursor",
        action="store_true",
        help="Une ligne dashboard KART Cursor (état local cursor-usage.json)",
    )
    parser.add_argument(
        "--set-cursor",
        nargs="*",
        metavar="KEY=VAL",
        help="Maj cursor-usage.json (composer_pct=4 api_pct=0 plan=pro)",
    )
    parser.add_argument(
        "--paid-only",
        action="store_true",
        help="Uniquement providers cloud facturés",
    )
    args = parser.parse_args()

    data = _repo_data_dir()
    os.environ.setdefault("DATA_DIR", str(data))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    from aria_core.testing import configure_test_runtime
    from aria_core.cursor_usage import format_cursor_usage_dashboard, update_cursor_usage
    from aria_core.llm_usage import (
        format_grok_build_dashboard,
        format_paid_usage_dashboard,
        paid_usage_snapshot,
        summarize_paid_usage,
        summarize_usage,
    )

    configure_test_runtime(data_dir=data)
    month = args.month or datetime.now(timezone.utc).strftime("%Y-%m")

    if args.set_cursor:
        kwargs: dict[str, object] = {}
        for item in args.set_cursor:
            if "=" not in item:
                continue
            key, val = item.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in ("composer", "composer_pct", "composer_pool_pct"):
                kwargs["composer_pool_pct"] = float(val)
            elif key in ("api", "api_pct", "api_pool_pct"):
                kwargs["api_pool_pct"] = float(val)
            elif key == "plan":
                kwargs["plan"] = val
        update_cursor_usage(**kwargs)
        print(format_cursor_usage_dashboard())
        return 0

    if args.cursor:
        print(format_cursor_usage_dashboard())
        return 0

    if args.compact or args.grok:
        print(format_grok_build_dashboard(month=month))
        return 0

    summary = (
        summarize_paid_usage(month=month)
        if args.paid_only
        else summarize_usage(month=month)
    )

    if args.json:
        payload = summary
        if args.paid_only:
            payload = {
                "summary": summary,
                "snapshot": paid_usage_snapshot(month=month),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    t = summary["totals"]
    print(f"=== LLM usage {month} ===")
    print(f"Appels OK: {t['calls_ok']} | échecs: {t['calls_failed']}")
    print(
        f"Tokens: in={t['input_tokens']:,} out={t['output_tokens']:,} "
        f"total={t['total_tokens']:,}"
    )
    if summary["by_day"]:
        print("\nPar jour:")
        for day, slot in summary["by_day"].items():
            print(
                f"  {day}: {slot['total_tokens']:,} tokens "
                f"({slot['calls']} appels)"
            )
    if summary["by_provider"]:
        print("\nPar provider:")
        for prov, slot in summary["by_provider"].items():
            print(
                f"  {prov}: {slot['total_tokens']:,} tokens "
                f"({slot['calls']} appels)"
            )
    if summary["by_model"]:
        print("\nPar modèle:")
        for model, slot in summary["by_model"].items():
            print(
                f"  {model}: {slot['total_tokens']:,} tokens "
                f"({slot['calls']} appels)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())