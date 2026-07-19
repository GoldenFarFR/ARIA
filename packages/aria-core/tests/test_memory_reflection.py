import pytest

from aria_core.memory.reflection import (
    append_reflection,
    clear_reflection_cache,
    get_reflections_text,
    read_explicit_reflections,
    reflections_count,
)
from aria_core.testing import configure_test_runtime


@pytest.fixture(autouse=True)
def _isolated_reflection(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    clear_reflection_cache()
    yield
    clear_reflection_cache()


def test_append_and_read_reflection():
    append_reflection("Deploy bloqué quota Render — retry mois prochain", context="deploy", outcome="blocked")
    items = read_explicit_reflections()
    assert len(items) == 1
    assert "quota Render" in items[0]["content"]
    assert reflections_count() == 1


def test_get_reflections_text_explicit():
    append_reflection("Priorité marketing avant nouveau POC", context="weekly", outcome="decision")
    text = get_reflections_text()
    assert "Réflexion opérationnelle ARIA" in text
    assert "Priorité marketing" in text
    assert "Réflexions enregistrées" in text


def test_sanitize_secrets_in_reflection():
    append_reflection("Token ghp-abcdefghijklmnop1234567890 exposé par erreur")
    text = get_reflections_text()
    assert "ghp_" not in text
    assert "[redacted]" in text


@pytest.mark.asyncio
async def test_build_llm_context_includes_reflection(monkeypatch):
    from aria_core.memory.arbitrator import ArbitrationResult
    from aria_core.memory.llm_context import build_llm_context

    append_reflection("Test reflection Phase G injectée")
    assert "Phase G injectée" in get_reflections_text()

    async def fake_arbitration(**kwargs):
        return ArbitrationResult()

    monkeypatch.setattr("aria_core.memory.collegue.get_collegue_text", lambda: "")
    monkeypatch.setattr("aria_core.memory.values.get_values_text", lambda: "")
    monkeypatch.setattr("aria_core.memory.goals.get_goals_text", lambda: "")
    monkeypatch.setattr("aria_core.memory.arbitrator.run_memory_arbitration", fake_arbitration)
    monkeypatch.setattr("aria_core.repertoire_db.get_messages", lambda *a, **k: [])

    ctx = await build_llm_context(public=False)
    assert "Réflexion opérationnelle ARIA" in ctx
    assert "Phase G injectée" in ctx


@pytest.mark.asyncio
async def test_build_llm_context_public_excludes_reflection():
    from aria_core.memory.llm_context import build_llm_context

    append_reflection("Secret operator reflection")
    ctx = await build_llm_context(public=True)
    assert "Réflexion opérationnelle ARIA" not in ctx