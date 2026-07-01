import pytest

from aria_core.memory.vector.chroma_client import chromadb_installed, reset_client_cache
from aria_core.memory.vector.chroma_store import is_available, search, store, vector_store_status
from aria_core.memory.vector.schema_validator import validate_entry

pytestmark = pytest.mark.skipif(
    not chromadb_installed(),
    reason="chromadb non installé — pip install -e '.[dev,vector]'",
)


@pytest.fixture(autouse=True)
def isolated_chroma(tmp_path, monkeypatch):
    from aria_core import paths as p

    monkeypatch.setattr(p, "chroma_dir", lambda: tmp_path / "chroma")
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.fixture
def vector_on(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_vector_memory", True)


def test_validate_entry_requires_metadata():
    ok, err = validate_entry("insight", {"source": "x"})
    assert ok is False
    assert "topic" in err
    ok2, _ = validate_entry("insight", {"source": "x", "topic": "gem"})
    assert ok2 is True


@pytest.mark.asyncio
async def test_store_search_when_enabled(vector_on):
    assert is_available() is True
    doc_id = await store(
        "lesson",
        "Cooldown Gem Crush 30 min sur GitHub",
        metadata={"topic": "gem_crush", "confidence": "0.9"},
    )
    assert doc_id
    hits = await search("cooldown gem crush", entry_type="lesson", limit=3)
    assert len(hits) >= 1
    assert any("cooldown" in (h.get("content") or "").lower() for h in hits)


@pytest.mark.asyncio
async def test_store_rejects_invalid_type(vector_on):
    assert await store("unknown_type", "text", metadata={}) is None


@pytest.mark.asyncio
async def test_search_empty_when_flag_off(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_vector_memory", False)
    reset_client_cache()
    assert is_available() is False
    assert await search("anything") == []


def test_status_reflects_install(vector_on):
    st = vector_store_status()
    assert st["enabled"] is True
    assert st["installed"] is True
    assert st["available"] is True
    assert "persist_dir" in st