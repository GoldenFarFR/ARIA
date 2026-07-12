import hashlib

import pytest

from aria_core.memory.vector.lancedb_client import lancedb_installed, reset_client_cache
from aria_core.memory.vector.lancedb_store import search

pytestmark = pytest.mark.skipif(
    not lancedb_installed(),
    reason="lancedb/fastembed non installés",
)


def _fake_vector(text: str) -> list[float]:
    """Même patron déterministe que test_lancedb_store.py — évite le réseau en CI."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 for b in (digest * 12)[:384]]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from aria_core import paths as p
    from aria_core.memory.vector import lancedb_store as ls

    monkeypatch.setattr(p, "vector_dir", lambda: tmp_path / "vector")
    monkeypatch.setattr(ls, "embed_text", _fake_vector)
    from aria_core.knowledge import cognitive as cog

    monkeypatch.setattr(p, "aria_db_path", lambda: tmp_path / "aria.db")
    monkeypatch.setattr(cog, "DB_PATH", str(tmp_path / "aria.db"))
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.mark.asyncio
async def test_approve_triggers_vector_ingest(monkeypatch):
    from aria_core.runtime import get_settings
    from aria_core.knowledge.cognitive import add_knowledge, approve_knowledge

    monkeypatch.setattr(get_settings(), "aria_vector_memory", True)
    text = "Les deploys Render groupent un seul redeploy par session"
    item = await add_knowledge("curiosity", "ship", text, approved=False)
    assert await approve_knowledge(item.id) is True
    hits = await search(text, entry_type="insight", limit=3)
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_approve_no_ingest_when_flag_off(monkeypatch):
    from aria_core.runtime import get_settings
    from aria_core.knowledge.cognitive import add_knowledge, approve_knowledge

    monkeypatch.setattr(get_settings(), "aria_vector_memory", False)
    text = "Leçon test flag off"
    item = await add_knowledge("manual", "ops", text, approved=False)
    await approve_knowledge(item.id)
    hits = await search(text, limit=5)
    assert hits == []
