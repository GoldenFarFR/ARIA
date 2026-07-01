#!/usr/bin/env python3
"""DEPRECATED — split aria-core terminé (2026-06-19).

Le cerveau vit dans aria-sandbox/packages/aria-core. Ne pas réexécuter.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "migrate_to_aria_core.py is obsolete — split already done.\n"
        "Edit aria-sandbox/packages/aria-core, then bump pin in backend/requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1)