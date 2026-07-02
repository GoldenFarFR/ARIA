import pytest

from aria_core.community_feedback import (
    _is_likely_english,
    assess_feedback_publishable_on_x,
    build_feedback_thanks_tweet,
    personal_take_on_feedback,
    queue_score_threshold,
    score_feedback,
    submit_community_feedback,
    translate_to_english_for_x,
)


def test_score_feedback_high_for_concrete_idea():
    s = score_feedback(
        "J'aimerais un bouton pour rejoindre la commu Telegram depuis le bandeau welcome du site Vanguard",
    )
    assert s >= 55


def test_score_feedback_low_for_spam():
    assert score_feedback("gm") == 0
    assert score_feedback("https://scam.com free money") <= 10


@pytest.mark.asyncio
async def test_submit_queues_worker(monkeypatch, tmp_path):
    from aria_core import community_feedback as mod

    monkeypatch.setattr(mod, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    async def fake_enqueue(**kwargs):
        return {"status": "local_md", "task_id": kwargs["task_id"]}

    async def noop_x(*a, **k):
        return {"status": "skipped", "reason": "test"}

    monkeypatch.setattr(
        "aria_core.aria_worker_queue.enqueue_worker_task",
        fake_enqueue,
    )
    monkeypatch.setattr(
        "aria_core.aria_worker_queue.sync_pending_local_tasks_to_md",
        lambda: [],
    )
    monkeypatch.setattr(mod, "maybe_tweet_community_feedback", noop_x)

    out = await submit_community_feedback(
        "Proposition : améliorer la FAQ Vanguard avec une section commu et lien Telegram",
        handle="builder42",
        visitor_id="v-test",
        lang="fr",
    )
    assert out["ok"] is True
    assert out["queued"] is True
    assert out["verdict"] == "queue"
    assert (tmp_path / "community-feedback.jsonl").is_file()


def test_queue_score_threshold_env(monkeypatch):
    monkeypatch.setenv("COMMUNITY_FEEDBACK_QUEUE_SCORE", "70")
    assert queue_score_threshold() == 70


def test_build_feedback_thanks_tweet_quotes_exact_feedback():
    excerpt = "I'd like a Telegram link on the Vanguard welcome banner"
    tweet = build_feedback_thanks_tweet(
        excerpt,
        handle="goldenfarfr",
        personal=personal_take_on_feedback(excerpt, lang="en"),
    )
    assert "@goldenfarfr" in tweet
    assert f'"{excerpt}"' in tweet
    assert tweet.startswith("@goldenfarfr · Vanguard site")
    assert "\n\n→ " in tweet
    assert len(tweet) <= 280


def test_personal_take_feedback_widget():
    text = (
        "Salut Aria c'est super de pouvoir te laisser un avis sur ton site web, "
        "on continue de construire ensemble"
    )
    reply = personal_take_on_feedback(text, lang="en")
    assert "feedback" in reply.lower()
    assert "love the energy" not in reply.lower()


def test_is_likely_english():
    assert _is_likely_english("Great site, add Telegram please")
    assert not _is_likely_english("Salut c'est joli le site bravo")


@pytest.mark.asyncio
async def test_translate_to_english_for_x(monkeypatch):
    async def fake_google(text: str) -> str:
        return "Hi Aria — great to leave feedback on your site, let's keep building together"

    async def noop_llm(_t: str) -> None:
        return None

    monkeypatch.setattr("aria_core.community_feedback._llm_translate_to_english", noop_llm)
    monkeypatch.setattr(
        "aria_core.community_feedback._google_translate_to_english",
        fake_google,
    )
    out, translated = await translate_to_english_for_x(
        "Salut bravo pour le site c'est joli",
    )
    assert translated is True
    assert "feedback" in out.lower()


@pytest.mark.asyncio
async def test_translate_skips_english():
    text = "Love the Vanguard site — please add Telegram"
    out, translated = await translate_to_english_for_x(text)
    assert translated is False
    assert out == text


def test_assess_feedback_blocks_profanity_and_noise():
    ok, reason = assess_feedback_publishable_on_x("ce site Vanguard c'est de la merde", score=60)
    assert not ok
    assert reason == "profanity"

    ok, reason = assess_feedback_publishable_on_x("gm", score=80)
    assert not ok
    assert reason == "low_substance"

    ok, reason = assess_feedback_publishable_on_x("bravo", score=80)
    assert not ok
    assert reason == "low_substance"

    text = (
        "Salut Aria c'est super de pouvoir te laisser un avis sur ton site web, "
        "on continue de construire ensemble"
    )
    ok, reason = assess_feedback_publishable_on_x(text, score=56)
    assert ok
    assert reason == "ok"


def test_assess_feedback_blocks_generic_low_score():
    ok, reason = assess_feedback_publishable_on_x("cool site bro", score=30)
    assert not ok
    assert reason == "score_too_low"


@pytest.mark.asyncio
async def test_maybe_tweet_skips_vulgar(monkeypatch):
    from aria_core import community_feedback as mod

    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    out = await mod.maybe_tweet_community_feedback(
        "putain de site nul",
        handle="troll",
        feedback_id="fb-mod-test",
        score=70,
        lang="fr",
    )
    assert out["status"] == "skipped"
    assert out["reason"] == "profanity"


def test_score_allows_holding_domain():
    text = (
        "Super travail sur ariavanguardzhc.com — avis interactifs intégrés "
        "pour la mémoire aria et le développement zhc"
    )
    assert score_feedback(text) >= 45


def test_trusted_handle_goldenfarfr():
    from aria_core.community_feedback import is_trusted_feedback_handle

    assert is_trusted_feedback_handle("GoldenFarFR")
    assert is_trusted_feedback_handle("@goldenfarfr")


@pytest.mark.asyncio
async def test_trusted_handle_posts_x_immediately(monkeypatch, tmp_path):
    from aria_core import community_feedback as mod

    monkeypatch.setattr(mod, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    async def noop_flush():
        return []

    async def fake_post(tweet, **kwargs):
        return None, "Publié sur X https://x.com/Aria_ZHC/status/1"

    monkeypatch.setattr(mod, "flush_due_community_x_tweets", noop_flush)
    monkeypatch.setattr("aria_core.gateway.x_twitter.post_tweet", fake_post)
    async def fake_compose(text_en, **kwargs):
        return mod.personal_take_on_feedback(text_en, lang="en")

    monkeypatch.setattr(mod, "compose_personal_reply_to_feedback", fake_compose)

    out = await mod.maybe_tweet_community_feedback(
        "Love the Vanguard site feedback form — we keep building together",
        handle="GoldenFarFR",
        visitor_id="visitor-1",
        feedback_id="fb-instant-test",
        score=50,
        lang="en",
    )
    assert out["status"] == "posted"


@pytest.mark.asyncio
async def test_community_tweet_queues_4h_cooldown_for_visitors(monkeypatch, tmp_path):
    from aria_core import community_feedback as mod

    monkeypatch.setattr(mod, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    async def noop_flush():
        return []

    monkeypatch.setattr(mod, "flush_due_community_x_tweets", noop_flush)

    out = await mod.maybe_tweet_community_feedback(
        "Love the Vanguard site feedback form — we keep building together",
        handle="visitor42",
        visitor_id="visitor-1",
        feedback_id="fb-queue-test",
        score=50,
        lang="en",
    )
    assert out["status"] == "queued"
    assert out["reason"] == "cooldown_4h"
    assert out.get("pending_count") == 1