"""Vérifie l'alignement écosystème — export JSON + checks PASS/FAIL."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    json_path = repo / "local-sync" / "ecosystem-registry.json"

    from aria_core.ecosystem_config import export_registry_json, verify_ecosystem_alignment

    export_registry_json(json_path)
    print(f"[export] {json_path}")

    checks = verify_ecosystem_alignment()
    fail = 0
    for row in checks:
        ok = bool(row.get("ok"))
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {row.get('name')} — {row.get('detail')}")
        if not ok:
            fail += 1
    if fail:
        print(f"\n=== ECOSYSTEM KO ({fail} echecs) ===", file=sys.stderr)
        return 1
    print("\n=== ECOSYSTEM OK ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())