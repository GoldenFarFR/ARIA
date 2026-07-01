import pytest

from aria_core.memory.vector.chroma_client import chromadb_installed, reset_client_cache
from aria_core.memory.vector.chroma_store import search

pytestmark = pytest.mark.skipif(
    not chromadb_installed(),
    reason="chromadb non installé",
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from aria_core import paths as p

    monkeypatch.setattr(p, "chroma_dir", lambda: tmp_path / "chroma")
    monkeypatch.setattr(p, "aria_db_path", lambda: tmp_path / "aria.db")
    from aria_core.knowledge import cognitive as cog

    monkeypatch.setattr(cog, "DB_PATH", str(tmp_path / "aria.db"))
    reset_client_cache()
    yield
    reset_client_cache()


@pytest.mark.asyncio
async def test_approve_triggers_vector_ingest(monkeypatch):
    from aria_core.runtime import get_settings
    from aria_core.knowledge.cognitive import add_knowledge, approve_knowledge

    monkeypatch.setattr(get_settings(), "aria_vector_memory", True)
    item = await add_knowledge(
        "curiosity",
        "ship",
        "Les releases Gem Crush groupent 10 items minimum",
        approved=False,
    )
    assert await approve_knowledge(item.id) is True
    hits = await search("gem crush releases", entry_type="insight", limit=3)
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_approve_no_ingest_when_flag_off(monkeypatch):
    from aria_core.runtime import get_settings
    from aria_core.knowledge.cognitive import add_knowledge, approve_knowledge

    monkeypatch.setattr(get_settings(), "aria_vector_memory", False)
    item = await add_knowledge("manual", "ops", "Leçon test flag off", approved=False)
    await approve_knowledge(item.id)
    hits = await search("leçon test", limit=5)
    assert hits == []