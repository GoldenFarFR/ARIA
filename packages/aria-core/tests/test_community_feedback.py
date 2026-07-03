import re

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
from aria_core.x_text import tweet_fits


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
    assert "@goldenfarfr" in tweet.lower()
    assert "ariavanguardzhc.com" in tweet
    assert f'"{excerpt}"' in tweet
    assert "→" not in tweet
    assert "Vanguard site" not in tweet
    assert tweet_fits(tweet)


def test_personal_take_answers_roadmap_questions():
    text = (
        "Super site ARIA! What's next for ZHC — partnerships and how will you generate revenue?"
    )
    reply = personal_take_on_feedback(text, lang="en")
    assert "ACP" in reply or "marketplace" in reply.lower()
    assert "good to hear on the site" not in reply.lower()


def test_build_feedback_tweet_roadmap_question():
    text = (
        "Great ARIA site — what's next for ZHC, partnerships, how will you generate revenue?"
    )
    personal = personal_take_on_feedback(text, lang="en")
    tweet = build_feedback_thanks_tweet(text, handle="GoldenFarFR", personal=personal)
    assert tweet_fits(tweet)
    assert "ariavanguardzhc.com" in tweet
    assert "ACP" in tweet or "marketplace" in tweet.lower()
    assert "→" not in tweet


def test_build_feedback_tweet_long_english_no_mid_sentence_cut():
    long_text = (
        "Aria is a fascinating and promising AI. I particularly appreciate her ability "
        "to memorize and evaluate the relevance of interactions to continuously improve "
        "— it's exactly what's needed for a community-driven product like Vanguard."
    )
    personal = "Glad you value memory and relevance scoring on Vanguard — that's the loop we optimize."
    tweet = build_feedback_thanks_tweet(long_text, handle="GoldenFarFR", personal=personal)
    assert tweet_fits(tweet)
    assert "@GoldenFarFR" in tweet or "@goldenfarfr" in tweet.lower()
    assert "…" not in tweet.split('"')[1] if '"' in tweet else True
    assert not re.search(r"\bfor\.\.\.", tweet)
    assert tweet.endswith(personal) or personal[:40] in tweet


def test_build_feedback_tweet_long_french_with_emoji():
    long_text = (
        "Salut Aria c'est super de pouvoir te laisser un avis sur ton site web "
        "ariavanguardzhc.com — on continue de construire ensemble la commu ZHC 🚀🔥 "
        + "avec plus de features pour Vanguard et le modèle crypto-first " * 3
    )
    tweet = build_feedback_thanks_tweet(
        long_text,
        handle="GoldenFarFR",
        personal=personal_take_on_feedback(long_text[:200], lang="en"),
    )
    assert tweet_fits(tweet)
    assert "@GoldenFarFR" in tweet or "@goldenfarfr" in tweet.lower()


def test_personal_take_feedback_widget():
    text = (
        "Salut Aria c'est super de pouvoir te laisser un avis sur ton site web, "
        "on continue de construire ensemble"
    )
    reply = personal_take_on_feedback(text, lang="en")
    assert "vanguard" in reply.lower() or "brick" in reply.lower()
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


def test_assess_feedback_blocks_internal_test():
    ok, reason = assess_feedback_publishable_on_x(
        "Super site web ARIA test diagnostic",
        score=80,
        handle="GoldenFarFR",
    )
    assert not ok
    assert reason == "internal_test"


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
    from aria_core.community_feedback import (
        is_trusted_feedback_handle,
        is_trusted_operator_publish,
    )

    assert is_trusted_feedback_handle("GoldenFarFR")
    assert is_trusted_feedback_handle("@goldenfarfr")
    assert is_trusted_operator_publish("GoldenFarFR")


def test_operator_bypasses_x_moderation():
    ok, reason = assess_feedback_publishable_on_x("bravo", score=5, handle="GoldenFarFR")
    assert ok
    assert reason == "ok_operator"


@pytest.mark.asyncio
async def test_operator_short_message_accepted(monkeypatch, tmp_path):
    from aria_core import community_feedback as mod

    monkeypatch.setattr(mod, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)
    monkeypatch.setattr(mod, "maybe_tweet_community_feedback", lambda *a, **k: {"status": "posted"})

    out = await submit_community_feedback("bravo site", handle="GoldenFarFR", lang="fr")
    assert out["ok"] is True
    assert out["verdict"] == "noted"
    assert out["queued"] is False
    assert out["score"] >= 80


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