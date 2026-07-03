"""Safe truth-ledger file I/O — atomic writes + cross-process lock (scaffold patterns)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

LOCK_TIMEOUT_SECONDS = 5


@contextmanager
def ledger_dir_lock(ledger_dir: Path):
    """File lock shared by all processes writing under the same ledger directory."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_dir / ".ledger-io.lock"
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=LOCK_TIMEOUT_SECONDS)
    except Timeout as exc:
        raise TimeoutError(
            f"Truth ledger I/O lock timeout ({ledger_dir})",
        ) from exc
    try:
        yield
    finally:
        lock.release()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write via temp file + replace — no partial reads by sync workers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def read_modify_write(
    path: Path,
    transform: Callable[[str], str],
    *,
    ledger_dir: Path,
    encoding: str = "utf-8",
    missing_ok: bool = False,
) -> bool:
    """Locked read-transform-write for status updates on existing markdown files."""
    if not path.exists():
        return False if missing_ok else False

    with ledger_dir_lock(ledger_dir):
        text = path.read_text(encoding=encoding)
        updated = transform(text)
        if updated == text:
            return True
        atomic_write_text(path, updated, encoding=encoding)
    return True