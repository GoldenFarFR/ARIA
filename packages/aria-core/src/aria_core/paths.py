"""Data paths — DATA_DIR set by host via bootstrap."""
from __future__ import annotations

import os
from pathlib import Path

_DATA_DIR: Path | None = None


def configure_data_dir(path: Path) -> None:
    global _DATA_DIR
    _DATA_DIR = Path(path)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def data_dir() -> Path:
    if _DATA_DIR is not None:
        return _DATA_DIR
    raw = os.getenv("DATA_DIR", "").strip()
    path = Path(raw) if raw else Path.cwd() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def aria_db_path() -> Path:
    return data_dir() / "aria.db"


def memory_dir() -> Path:
    path = data_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def truth_ledger_dir() -> Path:
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


def chroma_dir() -> Path:
    """Persistance Chroma embedded — Phase C (opt-in via aria_vector_memory)."""
    path = data_dir() / "chroma"
    path.mkdir(parents=True, exist_ok=True)
    return path
