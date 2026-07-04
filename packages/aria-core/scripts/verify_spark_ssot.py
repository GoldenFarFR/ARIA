"""Verify Spark SSOT alignment — exit 0 when all checks PASS."""
from __future__ import annotations

import sys

from aria_core.spark_config import verify_spark_alignment


def main() -> int:
    checks = verify_spark_alignment()
    fail = 0
    for row in checks:
        ok = bool(row.get("ok"))
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {row.get('name')} — {row.get('detail')}")
        if not ok:
            fail += 1
    if fail:
        print(f"\n=== SPARK SSOT KO ({fail} failures) ===", file=sys.stderr)
        return 1
    print("\n=== SPARK SSOT OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())