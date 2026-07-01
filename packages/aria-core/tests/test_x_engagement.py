import pytest

from aria_core.gateway import x_engagement


def test_mentions_learn_disabled_by_default(test_settings):
    import aria_core.gateway.x_engagement as mod

    test_settings.x_mentions_learn_enabled = False
    test_settings.x_api_key = "k"
    test_settings.x_bearer_token = "b"
    assert mod.mentions_learn_enabled() is False


def test_mentions_reply_enabled_without_learn(test_settings):
    import aria_core.gateway.x_engagement as mod

    test_settings.x_mentions_learn_enabled = False
    test_settings.x_allow_replies = True
    test_settings.x_api_key = "k"
    test_settings.x_api_secret = "s"
    test_settings.x_access_token = "t"
    test_settings.x_access_token_secret = "ts"
    test_settings.x_bearer_token = "b"
    assert mod.mentions_learn_enabled() is False
    assert mod.mentions_reply_enabled() is True


async def test_mentions_cycle_disabled_without_oauth(monkeypatch):
    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: False)
    result = await x_engagement.run_mentions_learn_cycle()
    assert result["status"] == "disabled"


@pytest.mark.asyncio
async def test_mentions_cycle_processes_and_likes(monkeypatch, tmp_path, test_settings):
    import aria_core.gateway.x_engagement as mod

    test_settings.aria_autonomous = True
    test_settings.x_allow_likes = True
    test_settings.x_mentions_learn_enabled = True
    test_settings.x_bearer_token = "bearer"
    test_settings.x_api_key = "k"
    test_settings.x_api_secret = "s"
    test_settings.x_access_token = "t"
    test_settings.x_access_token_secret = "ts"

    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(mod, "LEDGER_PATH", ledger)
    monkeypatch.setattr(
        "aria_core.gateway.x_engagement._verify_me_sync",
        lambda: {"id": "u1", "username": "Aria_ZHC"},
    )

    def fake_fetch(_uid, _since):
        return (
            [{
                "tweet_id": "999",
                "text": "For ZHC autonomy: prioritize DEXPulse signal brief as first paid micro-product.",
                "username": "solvrbot",
                "author_id": "a2",
            }],
            "999",
            "999",
        )

    monkeypatch.setattr("aria_core.gateway.x_engagement._fetch_mentions_sync", fake_fetch)

    liked: list[str] = []

    def fake_like(_uid, tid):
        liked.append(tid)
        return True

    monkeypatch.setattr("aria_core.gateway.x_engagement._like_tweet_sync", fake_like)

    async def noop_notify(_text):
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.notify_admin", noop_notify)

    from aria_core.knowledge.x_insight_relevance import InsightAssessment

    async def fake_assess(_text, *, source="x_mention"):
        return InsightAssessment(
            store=True,
            pertinent=True,
            truth="true",
            reason="test",
            confidence=0.9,
            groq_used=True,
        )

    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_x_insight_for_memory",
        fake_assess,
    )

    result = await mod.run_mentions_learn_cycle()
    assert result["status"] == "ok"
    assert result["processed"] == 1
    assert result["liked"] == 1
    assert liked == ["999"]


@pytest.mark.asyncio
async def test_mentions_cycle_replies_without_like(monkeypatch, tmp_path, test_settings):
    import aria_core.gateway.x_engagement as mod

    test_settings.aria_autonomous = False
    test_settings.x_allow_likes = False
    test_settings.x_allow_replies = True
    test_settings.x_mentions_learn_enabled = True
    test_settings.x_bearer_token = "bearer"
    test_settings.x_api_key = "k"
    test_settings.x_api_secret = "s"
    test_settings.x_access_token = "t"
    test_settings.x_access_token_secret = "ts"

    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(mod, "LEDGER_PATH", ledger)
    monkeypatch.setattr(
        "aria_core.gateway.x_engagement._verify_me_sync",
        lambda: {"id": "u1", "username": "Aria_ZHC"},
    )

    def fake_fetch(_uid, _since):
        return (
            [{
                "tweet_id": "888",
                "text": "What is your roadmap for Vanguard ZHC this quarter?",
                "username": "builder42",
                "author_id": "a3",
            }],
            "888",
            "888",
        )

    monkeypatch.setattr("aria_core.gateway.x_engagement._fetch_mentions_sync", fake_fetch)

    async def fake_compose(_user, _text):
        return "We ship in public — site, signals, and agent skills first. What would you track?"

    monkeypatch.setattr(mod, "compose_mention_reply", fake_compose)

    replied: list[tuple[str, str]] = []

    async def fake_reply(text, *, in_reply_to_tweet_id, approval_id="x_mention", force=False):
        replied.append((in_reply_to_tweet_id, text))
        return "777", "ok"

    monkeypatch.setattr("aria_core.gateway.x_engagement.reply_to_tweet", fake_reply)

    from aria_core.knowledge.x_insight_relevance import InsightAssessment

    async def fake_assess(_text, *, source="x_mention"):
        return InsightAssessment(
            store=False,
            pertinent=False,
            truth="unknown",
            reason="test skip store",
            confidence=0.2,
            groq_used=True,
        )

    monkeypatch.setattr(
        "aria_core.knowledge.x_insight_relevance.assess_x_insight_for_memory",
        fake_assess,
    )

    result = await mod.run_mentions_learn_cycle()
    assert result["status"] == "ok"
    assert result["replied"] == 1
    assert result["liked"] == 0
    assert replied == [("888", "We ship in public — site, signals, and agent skills first. What would you track?")]