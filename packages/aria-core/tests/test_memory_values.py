import pytest

from aria_core.memory.values import clear_values_cache, get_values_text, values_count


@pytest.fixture(autouse=True)
def _fresh_values():
    clear_values_cache()
    yield
    clear_values_cache()


def test_values_count():
    assert values_count() >= 6


def test_get_values_text_contains_core_principles():
    text = get_values_text()
    assert "Valeurs opérationnelles ARIA" in text
    assert "Autonomie progressive" in text
    assert "DuckDuckGo" in text or "gratuit" in text.lower()


@pytest.mark.asyncio
async def test_build_llm_context_includes_values(monkeypatch):
    from aria_core.memory.llm_context import build_llm_context
    from aria_core.testing import configure_test_runtime

    configure_test_runtime()
    ctx = await build_llm_context(public=False)
    assert "Valeurs opérationnelles ARIA" in ctx
    assert "Self-improvement" in ctx or "self" in ctx.lower()