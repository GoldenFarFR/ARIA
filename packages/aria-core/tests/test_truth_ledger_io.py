from pathlib import Path

import pytest

from aria_core.truth_ledger.io import atomic_write_text, ledger_dir_lock, read_modify_write


def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / "day" / "entry.md"
    atomic_write_text(target, "hello ledger")
    assert target.read_text(encoding="utf-8") == "hello ledger"


def test_read_modify_write_status(tmp_path):
    target = tmp_path / "entry.md"
    target.write_text("status: pending\nbody", encoding="utf-8")

    def to_verified(text: str) -> str:
        return text.replace("status: pending", "status: verified", 1)

    ok = read_modify_write(target, to_verified, ledger_dir=tmp_path)
    assert ok is True
    assert "status: verified" in target.read_text(encoding="utf-8")


def test_ledger_dir_lock_serializes(tmp_path):
    order: list[int] = []

    def work(n: int) -> None:
        with ledger_dir_lock(tmp_path):
            order.append(n)

    work(1)
    work(2)
    assert order == [1, 2]