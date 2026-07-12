import pytest

from aria_core.memory import (
    append,
    append_memory,
    count_memory_entries,
    is_vector_enabled,
    vector_store_status,
)
from aria_core.memory.vector.lancedb_store import search, store


def test_legacy_append_memory_still_works(tmp_path, monkeypatch):
    from aria_core import memory as mem_pkg
    from aria_core.memory import _legacy_journal as leg

    monkeypatch.setattr(leg, "MEMORY_DIR", tmp_path / "memory")
    path = append_memory("test", "hello legacy")
    assert "test_" in path
    assert count_memory_entries() >= 1


def test_facade_append_alias(tmp_path, monkeypatch):
    from aria_core.memory import _legacy_journal as leg

    monkeypatch.setattr(leg, "MEMORY_DIR", tmp_path / "memory")
    append("test", "hello facade")
    assert count_memory_entries() >= 1


def test_vector_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_VECTOR_MEMORY", raising=False)
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_vector_memory", False)
    assert is_vector_enabled() is False
    status = vector_store_status()
    assert status["enabled"] is False
    assert status["available"] is False
    assert "insight" in status["entry_types"]


@pytest.mark.asyncio
async def test_vector_store_noop_when_disabled():
    assert await store("insight", "test content") is None
    assert await search("query") == []


def test_vector_flag_opt_in(monkeypatch):
    from aria_core.memory.vector.lancedb_client import lancedb_installed, reset_client_cache
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_vector_memory", True)
    reset_client_cache()
    assert is_vector_enabled() is True
    status = vector_store_status()
    if lancedb_installed():
        assert status["installed"] is True
    else:
        assert status["available"] is False