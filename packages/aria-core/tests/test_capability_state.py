import pytest

from aria_core.memory.capability_state import get_capability_state_text


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var, _ in [
        ("ARIA_WEB_FETCH_ENABLED", None),
        ("ARIA_WALLET_SCORING_ENABLED", None),
        ("ARIA_VISION_ENABLED", None),
        ("ARIA_WEB_SEARCH_PROVIDER", None),
        ("ARIA_AGENT_WALLET_PILOT_ENABLED", None),
        ("ARIA_AGENT_WALLET_TRANSFER_ENABLED", None),
        ("ARIA_AGENT_WALLET_MONITOR_ENABLED", None),
    ]:
        monkeypatch.delenv(var, raising=False)
    yield


def test_reflects_disabled_by_default():
    text = get_capability_state_text()
    assert "Lecture directe d'une page web" in text
    assert "ARIA_WEB_FETCH_ENABLED" in text
    assert "inactive" in text


def test_reflects_enabled_when_env_set(monkeypatch):
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "true")
    text = get_capability_state_text()
    lines = [ln for ln in text.splitlines() if "ARIA_WEB_FETCH_ENABLED" in ln]
    assert lines and "ACTIVE" in lines[0]


def test_reflects_toggle_off_after_being_on(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_SCORING_ENABLED", "true")
    assert "ACTIVE" in [
        ln for ln in get_capability_state_text().splitlines() if "ARIA_WALLET_SCORING_ENABLED" in ln
    ][0]
    monkeypatch.delenv("ARIA_WALLET_SCORING_ENABLED", raising=False)
    assert "inactive" in [
        ln for ln in get_capability_state_text().splitlines() if "ARIA_WALLET_SCORING_ENABLED" in ln
    ][0]


def test_web_search_provider_defaults_to_ddg():
    text = get_capability_state_text()
    assert "ddg" in text.lower()


def test_web_search_provider_reflects_tavily(monkeypatch):
    monkeypatch.setenv("ARIA_WEB_SEARCH_PROVIDER", "tavily")
    text = get_capability_state_text()
    assert "tavily" in text.lower()


def test_agent_wallet_pilot_marked_as_real_money_when_active(monkeypatch):
    """20/07 -- gap réel trouvé en auditant une auto-description d'ARIA affirmant
    "zéro capital réel, capital ensuite" alors que le pilote agent-wallet est actif
    en prod depuis le 18/07 : ce registre ne le mentionnait nulle part. Le libellé
    doit dire explicitement ARGENT RÉEL, pas juste le nom technique du gate."""
    text = get_capability_state_text()
    lines = [ln for ln in text.splitlines() if "ARIA_AGENT_WALLET_PILOT_ENABLED" in ln]
    assert lines and "inactive" in lines[0]
    assert "ARGENT RÉEL" in lines[0]

    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    text = get_capability_state_text()
    lines = [ln for ln in text.splitlines() if "ARIA_AGENT_WALLET_PILOT_ENABLED" in ln]
    assert lines and "ACTIVE" in lines[0]
    assert "ARGENT RÉEL" in lines[0]


@pytest.mark.asyncio
async def test_build_llm_context_includes_capability_state(monkeypatch):
    from aria_core.memory.llm_context import build_llm_context
    from aria_core.testing import configure_test_runtime

    configure_test_runtime()
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "true")
    ctx = await build_llm_context(public=False)
    assert "État des capacités ARIA" in ctx
    lines = [ln for ln in ctx.splitlines() if "ARIA_WEB_FETCH_ENABLED" in ln]
    assert lines and "ACTIVE" in lines[0]


@pytest.mark.asyncio
async def test_build_llm_context_includes_live_pocket_state(monkeypatch, tmp_path):
    """06/08 -- operator finding, live (screenshot): a free-text question
    about a retired pocket fell through to an unrelated skill match because
    nothing in aria_brain's context knew it existed. Locks that the pocket
    list is now part of every non-public conversational context, always
    derived from the real pocket lineup, never a frozen doc."""
    from aria_core import paper_trader as pt
    from aria_core.memory.llm_context import build_llm_context
    from aria_core.testing import configure_test_runtime

    configure_test_runtime()
    monkeypatch.setattr(pt, "DB_PATH", str(tmp_path / "paper.db"))
    await pt.reset_portfolio(1_000_000.0, wallet="swing")
    ctx = await build_llm_context(public=False)
    assert "Poches de trading actives" in ctx
    assert "Swing" in ctx
