import pytest

from aria_core.gateway.x_twitter import is_placeholder_x_insight


def test_placeholder_insight_detected():
    text = "ARIA ZHC — configure X_BEARER_TOKEN (read) et OAuth keys (post) pour @Aria_ZHC."
    assert is_placeholder_x_insight(text) is True
    assert is_placeholder_x_insight(text, "x_setup") is True


def test_real_tweet_not_placeholder():
    assert is_placeholder_x_insight("DEXPulse v2 ships this week — on-chain signals live.") is False


@pytest.mark.asyncio
async def test_fetch_curiosity_feed_no_mock_when_unconfigured(monkeypatch):
    from aria_core.gateway import x_twitter
    from aria_core.testing import reload_test_settings

    reload_test_settings(monkeypatch, X_BEARER_TOKEN="")
    items = await x_twitter.fetch_curiosity_feed()
    assert items == []


@pytest.mark.asyncio
async def test_purge_placeholder_insights(monkeypatch, tmp_path):
    from aria_core.knowledge import cognitive
    from aria_core.testing import reload_test_settings

    db = tmp_path / "aria.db"
    monkeypatch.setattr(cognitive, "DB_PATH", str(db))
    reload_test_settings(monkeypatch)

    await cognitive.add_knowledge(
        source="x_twitter",
        topic="x_setup",
        content="ARIA ZHC — configure X_BEARER_TOKEN (read) et OAuth keys (post) pour @Aria_ZHC.",
        approved=True,
    )
    await cognitive.add_knowledge(
        source="x_twitter",
        topic="@base",
        content="Base ecosystem weekly recap.",
        approved=True,
    )

    removed = await cognitive.purge_placeholder_insights()
    assert removed == 1
    remaining = await cognitive.get_approved()
    assert len(remaining) == 1
    assert remaining[0].topic == "@base"