"""Central data directory — mount Render persistent disk on this path."""

from __future__ import annotations

import os
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    path = Path(raw) if raw else _BACKEND_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def product_db_path() -> Path:
    # Legacy on-disk name (pre-rebrand) — kept for Render persistent disk compatibility.
    return data_dir() / "dexpulse.db"


def auth_db_path() -> Path:
    return data_dir() / "auth.db"


def aria_db_path() -> Path:
    return data_dir() / "aria.db"


def memory_dir() -> Path:
    path = data_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def truth_ledger_dir() -> Path:
    """Local mirror of aria-sandbox/truth-ledger — mega history of verified exchanges."""
    path = data_dir() / "truth-ledger"
    path.mkdir(parents=True, exist_ok=True)
    return path


def aria_avatar_dir() -> Path:
    path = data_dir() / "aria" / "avatar"
    path.mkdir(parents=True, exist_ok=True)
    return path


def aria_avatar_gallery_dir() -> Path:
    path = aria_avatar_dir() / "gallery"
    path.mkdir(parents=True, exist_ok=True)
    return path