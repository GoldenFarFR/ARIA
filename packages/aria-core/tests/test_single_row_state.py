"""Shared single-row SQLite state plumbing (10/08) -- isolated temp DB.
Concurrency test added the same day after the Devil's Advocate flagged
(twice, independently) that read()+write() as two separate connections
could lose increments under concurrent callers on the same global row."""
from __future__ import annotations

import asyncio

import pytest

from aria_core.single_row_state import SingleRowStore


def _store(tmp_path) -> SingleRowStore:
    return SingleRowStore(
        str(tmp_path / "single_row_test.db"),
        "test_state",
        [("counter", "INTEGER NOT NULL DEFAULT 0", 0)],
    )


@pytest.mark.asyncio
async def test_read_before_any_write_returns_defaults(tmp_path):
    store = _store(tmp_path)
    row = await store.read("counter")
    assert row == (0,)


@pytest.mark.asyncio
async def test_write_then_read_roundtrips(tmp_path):
    store = _store(tmp_path)
    await store.write({"counter": 42})
    row = await store.read("counter")
    assert row == (42,)


@pytest.mark.asyncio
async def test_mutate_applies_values_and_returns_result(tmp_path):
    store = _store(tmp_path)

    def _incr(row):
        current = row[0] if row else 0
        return {"counter": current + 1}, current + 1

    result = await store.mutate(("counter",), _incr)
    assert result == 1
    row = await store.read("counter")
    assert row == (1,)


@pytest.mark.asyncio
async def test_mutate_with_no_values_is_read_only(tmp_path):
    store = _store(tmp_path)
    await store.write({"counter": 5})

    def _peek(row):
        return None, row[0]

    result = await store.mutate(("counter",), _peek)
    assert result == 5
    row = await store.read("counter")
    assert row == (5,)  # untouched


@pytest.mark.asyncio
async def test_concurrent_mutate_never_loses_an_increment(tmp_path):
    """The exact race the Devil's Advocate flagged: N concurrent callers
    each incrementing the SAME global row must sum to exactly N, never
    less (a lost increment would mean two callers both read the same
    stale value and overwrote each other's write)."""
    store = _store(tmp_path)

    def _incr(row):
        current = row[0] if row else 0
        return {"counter": current + 1}, current + 1

    N = 20
    await asyncio.gather(*(store.mutate(("counter",), _incr) for _ in range(N)))
    row = await store.read("counter")
    assert row == (N,)
