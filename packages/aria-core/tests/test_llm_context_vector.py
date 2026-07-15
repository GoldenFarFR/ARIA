import pytest

from aria_core.memory.llm_context import (
    build_llm_context,
    fetch_vector_recall,
    sanitize_recall_text,
)


def test_sanitize_recall_redacts_api_keys():
    raw = "cle api_key=supersecret123 token sk-abcdefghijklmnop"
    out = sanitize_recall_text(raw)
    assert "supersecret" not in out
    assert "sk-abcdefghij" not in out
    assert "[redacted]" in out


@pytest.mark.asyncio
async def test_fetch_vector_recall_disabled():
    assert await fetch_vector_recall("question longue sur ARIA memoire") == ""


@pytest.mark.asyncio
async def test_fetch_vector_recall_short_query():
    from aria_core.runtime import get_settings

    settings = get_settings()
    original = settings.aria_vector_memory
    settings.aria_vector_memory = True
    try:
        assert await fetch_vector_recall("court") == ""
    finally:
        settings.aria_vector_memory = original


@pytest.mark.asyncio
async def test_fetch_vector_recall_with_mock(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_vector_memory", True)

    async def fake_search(query: str, *, entry_type=None, limit=8):
        return [
            {
                "content": "Leçon deploy Render groupé",
                "metadata": {"entry_type": "lesson", "topic": "ops"},
            },
            {
                "content": "api_key=SECRET123",
                "metadata": {"entry_type": "insight", "topic": "bad"},
            },
        ]

    from aria_core.memory import vector as vec_mod

    monkeypatch.setattr(vec_mod, "search", fake_search)

    out = await fetch_vector_recall("comment deployer aria sur render")
    assert "Leçon deploy" in out
    assert "SECRET123" not in out


@pytest.mark.asyncio
async def test_build_llm_context_injects_vector_section(monkeypatch):
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_vector_memory", True)

    async def fake_search(query: str, *, entry_type=None, limit=8):
        return [
            {
                "content": "Priorité mémoire vectorielle locale",
                "metadata": {"entry_type": "lesson", "topic": "memory"},
            },
        ]

    monkeypatch.setattr("aria_core.memory.vector.search", fake_search)
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_persona_text", lambda: "persona courte")
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_doctrine_text", lambda: "")
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_launchpad_doctrine_text", lambda: "")
    monkeypatch.setattr("aria_core.directives.get_directives_text", lambda: "")

    async def fake_messages(*args, **kwargs):
        return [{"role": "user", "content": "rappelle moi la mémoire vectorielle"}]

    monkeypatch.setattr("aria_core.repertoire_db.get_messages", fake_messages)
    async def fake_approved():
        return []

    monkeypatch.setattr("aria_core.knowledge.cognitive.get_approved", fake_approved)

    async def fake_arbitration(**kwargs):
        from aria_core.memory.arbitrator import ArbitrationResult

        return ArbitrationResult()

    monkeypatch.setattr(
        "aria_core.memory.arbitrator.run_memory_arbitration",
        fake_arbitration,
    )
    monkeypatch.setattr("aria_core.memory.collegue.get_collegue_text", lambda: "")

    ctx = await build_llm_context(public=False, query_hint="mémoire vectorielle ARIA")
    assert "Rappel sémantique" in ctx
    assert "Priorité mémoire vectorielle" in ctx


@pytest.mark.asyncio
async def test_build_llm_context_filters_suppressed_journal_entries(monkeypatch):
    """Balayage code mort du 15/07 : suppressed_journal_preview (arbitrator.py) était
    écrite mais jamais appliquée ici -- un extrait de journal marqué supprimé par
    l'arbitrage (conflit avec une couche prioritaire) restait quand même injecté dans
    le contexte LLM tel quel via get_journal_summary(). Même patron de test que
    test_build_llm_context_injects_vector_section pour la couche "vector"."""
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_persona_text", lambda: "persona courte")
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_doctrine_text", lambda: "")
    monkeypatch.setattr("aria_core.memory._legacy_journal.get_launchpad_doctrine_text", lambda: "")
    monkeypatch.setattr("aria_core.directives.get_directives_text", lambda: "")

    async def fake_messages(*args, **kwargs):
        return [{"role": "user", "content": "rappelle le journal"}]

    monkeypatch.setattr("aria_core.repertoire_db.get_messages", fake_messages)

    async def fake_approved():
        return []

    monkeypatch.setattr("aria_core.knowledge.cognitive.get_approved", fake_approved)
    monkeypatch.setattr("aria_core.memory.collegue.get_collegue_text", lambda: "")
    monkeypatch.setattr(
        "aria_core.memory.llm_context.get_journal_summary",
        lambda: "Entrée gardée, sans rapport\n---\nEntrée à supprimer par arbitrage",
    )

    async def fake_arbitration(**kwargs):
        from aria_core.memory.arbitrator import ArbitrationResult, MemorySnippet

        return ArbitrationResult(
            suppressed=[
                MemorySnippet(
                    layer="journal", tier="low", priority=1,
                    content="Entrée à supprimer par arbitrage",
                )
            ],
            conflicts=[{"reason": "test"}],
        )

    monkeypatch.setattr(
        "aria_core.memory.arbitrator.run_memory_arbitration",
        fake_arbitration,
    )

    ctx = await build_llm_context(public=False, query_hint="journal")
    assert "Entrée gardée, sans rapport" in ctx
    assert "Entrée à supprimer par arbitrage" not in ctx


@pytest.mark.asyncio
async def test_build_llm_context_no_vector_when_public(monkeypatch):
    from aria_core.runtime import get_settings

    settings = get_settings()
    settings.aria_vector_memory = True

    called = {"search": False}

    async def fake_search(*args, **kwargs):
        called["search"] = True
        return []

    from aria_core.memory import vector as vec_mod

    monkeypatch.setattr(vec_mod, "search", fake_search)
    monkeypatch.setattr("aria_core.repertoire_db.get_messages", lambda *a, **k: [])

    ctx = await build_llm_context(public=True)
    assert "Rappel sémantique" not in ctx
    assert called["search"] is False