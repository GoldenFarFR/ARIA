import json

import pytest

from aria_core.memory.arbitrator import (
    MemorySnippet,
    arbitrate_snippets,
    clear_arbitrator_cache,
    collect_memory_snippets,
    get_arbitration_text,
    is_arbitrator_enabled,
    log_arbitration,
    run_memory_arbitration,
)
from aria_core.testing import configure_test_runtime


@pytest.fixture(autouse=True)
def _isolated_arbitrator(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    clear_arbitrator_cache()
    yield
    clear_arbitrator_cache()


def test_is_arbitrator_enabled_default():
    assert is_arbitrator_enabled() is True


@pytest.mark.asyncio
async def test_collect_snippets_has_layers():
    snippets = await collect_memory_snippets(messages=[{"role": "user", "content": "hello test"}])
    layers = {s.layer for s in snippets}
    assert "conversation" in layers
    assert "values" in layers or "goals" in layers


def test_arbitrate_suppresses_holding_conflict():
    snippets = [
        MemorySnippet("truth_ledger", "long", 90, "DEXPulse est une filiale, pas la holding.", "holding-vs-dexpulse"),
        MemorySnippet("journal", "medium", 50, "DEXPulse est la holding mère du groupe.", "journal-1"),
        MemorySnippet("directive", "short", 100, "Priorité marketing Q3", "d1"),
    ]
    result = arbitrate_snippets(snippets)
    kept_layers = {s.layer for s in result.kept}
    suppressed_layers = {s.layer for s in result.suppressed}
    assert "directive" in kept_layers
    assert "truth_ledger" in kept_layers
    assert "journal" in suppressed_layers
    assert len(result.conflicts) >= 1


def test_get_arbitration_text_with_conflicts():
    snippets = [
        MemorySnippet("journal", "medium", 50, "DEXPulse est la holding mère.", "j1"),
    ]
    result = arbitrate_snippets(snippets)
    text = get_arbitration_text(result)
    if result.conflicts:
        assert "Arbitre mémoire ARIA" in text
        assert "Conflits résolus" in text
        assert "journal" in text


def test_log_arbitration_writes_jsonl(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    result = arbitrate_snippets([
        MemorySnippet("values", "medium", 70, "Autonomie progressive", "v1"),
    ])
    log_arbitration(result)
    log_file = tmp_path / "memory" / "arbitration.jsonl"
    assert log_file.is_file()
    entry = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["kept"] >= 1


@pytest.mark.asyncio
async def test_build_llm_context_includes_arbitrator():
    from aria_core.memory.llm_context import build_llm_context

    ctx = await build_llm_context(public=False)
    assert "Arbitre mémoire ARIA" in ctx or "Hiérarchie" in ctx


@pytest.mark.asyncio
async def test_build_llm_context_public_excludes_arbitrator():
    from aria_core.memory.llm_context import build_llm_context

    ctx = await build_llm_context(public=True)
    assert "Arbitre mémoire ARIA" not in ctx


@pytest.mark.asyncio
async def test_run_memory_arbitration_disabled(monkeypatch):
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_memory_arbitrator", False)
    result = await run_memory_arbitration()
    assert result.kept == []
    assert result.suppressed == []