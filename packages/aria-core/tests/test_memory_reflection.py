import json

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
async def test_build_llm_context_includes_reflection():
    from aria_core.memory.llm_context import build_llm_context

    append_reflection("Test reflection Phase G injectée")
    ctx = await build_llm_context(public=False)
    assert "Réflexion opérationnelle ARIA" in ctx
    assert "Phase G injectée" in ctx


@pytest.mark.asyncio
async def test_build_llm_context_public_excludes_reflection():
    from aria_core.memory.llm_context import build_llm_context

    append_reflection("Secret operator reflection")
    ctx = await build_llm_context(public=True)
    assert "Réflexion opérationnelle ARIA" not in ctx


@pytest.mark.asyncio
async def test_qi_judge_appends_reflection(tmp_path, monkeypatch):
    from aria_core import capability_levels as cl
    from aria_core.qi_auto_judge import run_qi_auto_judge

    monkeypatch.setattr(cl, "PROGRESS_PATH", tmp_path / "capability_progress.json")
    monkeypatch.setattr(
        "aria_core.qi_self_judge_shadow.run_qi_judge_with_shadow",
        lambda **_: (_ for _ in ()).throw(RuntimeError("skip shadow")),
    )
    await run_qi_auto_judge(source="test", lang="fr")
    log = tmp_path / "memory" / "reflections.jsonl"
    if log.is_file():
        entry = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry.get("context") == "qi-judge"