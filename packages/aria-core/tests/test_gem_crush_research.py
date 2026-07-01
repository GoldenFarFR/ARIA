import pytest

from aria_core.skills.gem_crush_research import (
    PILLARS,
    TARGET_COMPETITORS,
    compose_brief_markdown,
    brief_path_for,
)
from aria_core.knowledge.web_verify import WebSource


def test_brief_path():
    assert brief_path_for(31) == "docs/gem-crush-briefs/v31.md"


def test_compose_brief_markdown_fr():
    sources = (WebSource(text="Royal Match uses light story and daily rewards", url="https://example.com"),)
    md = compose_brief_markdown(
        version=31,
        release_title="Prestige Chapitre I",
        sources=sources,
        planned_items=("Narrative", "Objectif doré"),
        lang="fr",
    )
    assert "v31" in md
    assert "Royal Match" in md
    assert "Narrative" in md
    for pillar in PILLARS:
        assert pillar in md
    for comp in TARGET_COMPETITORS[:3]:
        assert comp in md
    assert "Concurrents ciblés" in md


@pytest.mark.asyncio
async def test_run_match3_research_mock(monkeypatch):
    from aria_core.skills import gem_crush_research as mod

    async def fake_fetch(_q: str, max_snippets: int = 4, **kwargs):
        return [WebSource(text="Candy Crush Saga retention features", url="https://x.com")]

    monkeypatch.setattr(mod, "fetch_web_snippets", fake_fetch)
    brief = await mod.run_match3_research(version=32, release_title="Test", lang="fr")
    assert brief.version == 32
    assert "Candy Crush" in brief.markdown
    assert len(brief.sources) >= 1