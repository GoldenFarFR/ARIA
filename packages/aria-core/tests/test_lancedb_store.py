import hashlib

import pytest

from aria_core.memory.vector.lancedb_client import lancedb_installed, reset_client_cache
from aria_core.memory.vector.lancedb_store import (
    is_available,
    search,
    store,
    vector_store_status,
)
from aria_core.memory.vector.schema_validator import validate_entry

pytestmark = pytest.mark.skipif(
    not lancedb_installed(),
    reason="lancedb/fastembed non installés — pip install -e '.[dev,vector]'",
)


def _fake_vector(text: str) -> list[float]:
    """Vecteur déterministe (hash) — évite toute dépendance réseau/téléchargement de
    modèle dans les tests unitaires. Un round-trip texte identique -> vecteur identique
    suffit à vérifier le câblage store()/search() ; la qualité sémantique du modèle
    fastembed réel n'est pas la responsabilité de cette suite (vérifiée manuellement,
    hors CI, cf. migration CVE-2026-45829)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 for b in (digest * 12)[:384]]


@pytest.fixture(autouse=True)
def isolated_lancedb(tmp_path, monkeypatch):
    from aria_core.memory.vector import lancedb_client as lc
    from aria_core.memory.vector import lancedb_store as ls

    monkeypatch.setattr(lc, "vector_dir", lambda: tmp_path / "vector")
    monkeypatch.setattr(ls, "embed_text", _fake_vector)
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
    text = "Cooldown deploy Render 2 min par pipeline"
    doc_id = await store(
        "lesson",
        text,
        metadata={"topic": "ops", "confidence": "0.9"},
    )
    assert doc_id
    hits = await search(text, entry_type="lesson", limit=3)
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


@pytest.mark.asyncio
async def test_store_upsert_same_source_id(vector_on):
    """Deux store() avec le même source_id -> mise à jour, pas duplication (merge_insert)."""
    meta = {"topic": "ops", "confidence": "0.9", "source_id": "fixed-id"}
    id1 = await store("lesson", "version un", metadata=meta)
    id2 = await store("lesson", "version deux", metadata=meta)
    assert id1 == id2 == "fixed-id"
    assert vector_store_status()["collection_count"] == 1


@pytest.mark.asyncio
async def test_search_filters_by_entry_type(vector_on):
    await store("lesson", "shared text alpha", metadata={"topic": "ops", "confidence": "1"})
    await store("insight", "shared text alpha", metadata={"source": "x", "topic": "ops"})
    hits = await search("shared text alpha", entry_type="lesson", limit=10)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_search_ignores_invalid_entry_type_filter(vector_on):
    """entry_type malformé : jamais un risque d'injection (la regex bloque toute
    interpolation dans le ``where``) — le filtre est juste ignoré, pas de crash, pas
    de faux résultat vide."""
    await store("lesson", "some content here", metadata={"topic": "ops", "confidence": "1"})
    hits = await search("some content here", entry_type="lesson'; DROP TABLE x; --", limit=10)
    assert len(hits) == 1
