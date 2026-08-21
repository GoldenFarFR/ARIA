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


def shadow_db_path() -> Path:
    """Dedicated SQLite file for the standalone shadow process
    (solana_pump_shadow/robinhood_pump_shadow/shadow_position_peak), which
    runs OUTSIDE Docker as its own long-lived Python process. 17/08 --
    real incident: these modules used to share ``aria_db_path()`` with the
    prod container, and two independent long-running processes writing to
    the same SQLite file (even in WAL mode) produced sustained ``database is
    locked`` failures on unrelated prod heartbeat tasks (wallet_scan_queue_cycle,
    candle_history_watchlist_cycle). A separate file removes the write
    contention entirely -- the shadow was always meant to be fully isolated
    from prod (never wired to the heartbeat, own throttle), this closes the
    one gap where it still shared state with it."""
    return data_dir() / "shadow.db"


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


def aria_marketing_video_dir() -> Path:
    path = data_dir() / "aria" / "marketing_video"
    path.mkdir(parents=True, exist_ok=True)
    return path


def relay_lessons_dir() -> Path:
    """25/07 -- one journal entry per relay exchange grounded in a real position
    (never every message, just the ones anchored in real data). Hors-repo, same
    doctrine as research-log.md (a raw stream at this volume would pollute the
    public repo's history) -- a future Claude Code session reads this journal
    and judges what's worth promoting into docs/aria-learning-inbox/ or a real
    code fix, never an automatic promotion."""
    path = data_dir() / "relay-lessons"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vector_dir() -> Path:
    """Embedded vector memory persistence — Phase C (opt-in via aria_vector_memory).

    Neutral name (engine-independent) — LanceDB since the CVE-2026-45829 migration
    (chromadb, unpatched server RCE). The old ``chroma/`` folder from a previous
    deployment is not migrated: vector memory disabled by default, near-zero volume
    when it was (188 KB), not a real dataset worth preserving.
    """
    path = data_dir() / "vector"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def ensure_wal(db) -> None:
    """Puts an open SQLite connection's database into WAL mode, idempotently.

    21/08 -- found by the Devil's Advocate and verified: `shadow.db` was in
    `delete` journal mode while `aria.db` had been in WAL for a long time, by
    manual configuration rather than by code. `delete` takes a whole-database
    write lock for every commit, so the shadow pockets' many small concurrent
    writers (entry inserts, exit updates, rejection log, integrity checks)
    serialise against each other and can surface as `SQLITE_BUSY` inside code
    paths written to never raise -- i.e. silently lost rows in exactly the
    data the pockets are judged on.

    The setting is persistent in the file, so this is belt-and-braces against
    a database recreated from scratch; it is called on table setup rather
    than on every connection, since re-issuing it per query would be a wasted
    round trip for a no-op. Never raises: an older SQLite, or a filesystem
    that cannot support WAL, must not stop a pocket from running.
    """
    try:
        await db.execute("PRAGMA journal_mode=WAL")
    except Exception:  # noqa: BLE001 -- a journal mode is never worth a crash
        pass
