import pytest

from aria_core.memory.goals import clear_goals_cache, get_goals_text, goals_count


@pytest.fixture(autouse=True)
def _fresh_goals():
    clear_goals_cache()
    yield
    clear_goals_cache()


def test_goals_count():
    assert goals_count() >= 5


def test_get_goals_text_contains_vision_priorities():
    text = get_goals_text()
    assert "Objectifs opérationnels ARIA" in text
    assert "marketing" in text.lower() or "Aria Market" in text
    assert "État actuel" in text or "QI global" in text or "Revenu" in text


@pytest.mark.asyncio
async def test_build_llm_context_includes_goals():
    from aria_core.memory.llm_context import build_llm_context
    from aria_core.testing import configure_test_runtime

    configure_test_runtime()
    ctx = await build_llm_context(public=False)
    assert "Objectifs opérationnels ARIA" in ctx
    assert "Valeurs opérationnelles ARIA" in ctx


@pytest.mark.asyncio
async def test_build_llm_context_public_excludes_goals():
    from aria_core.memory.llm_context import build_llm_context
    from aria_core.testing import configure_test_runtime

    configure_test_runtime()
    ctx = await build_llm_context(public=True)
    assert "Objectifs opérationnelles ARIA" not in ctx