import json
from datetime import datetime, timedelta, timezone

from aria_core.x_publication_policy import (
    LEDGER_PATH,
    X_COST_USD,
    X_SAFE_MAX_POSTS_PER_15MIN,
    _estimate_tweet_cost,
    _save_ledger,
    check_engagement_allowed,
    check_reply_allowed,
    check_tweet_allowed,
    check_tweet_content,
    list_published_tweets,
    record_tweet_posted,
    record_x_rate_limit,
)


def test_tweet_cost_without_url():
    assert _estimate_tweet_cost("Hello ARIA") == X_COST_USD["tweet"]


def test_tweet_cost_with_url_blocked_by_default():
    allowed, reason, cost = check_tweet_allowed("Check https://example.com now")
    assert not allowed
    assert cost == X_COST_USD["tweet_with_url"]
    assert "URL" in reason


def test_empty_tweet_blocked():
    allowed, reason, _ = check_tweet_allowed("   ")
    assert not allowed
    assert "vide" in reason.lower()


def test_like_blocked_by_default():
    allowed, reason, cost = check_engagement_allowed("like")
    assert not allowed
    assert cost == X_COST_USD["like"]
    assert "X_ALLOW_LIKES" in reason


def test_reply_blocked_by_default():
    allowed, reason, cost = check_reply_allowed("Thanks for the question — we ship in public.")
    assert not allowed
    assert cost == X_COST_USD["reply"]
    assert "X_ALLOW_REPLIES" in reason


def test_reply_allowed_when_flag_on(test_settings):
    test_settings.x_allow_replies = True
    allowed, reason, cost = check_reply_allowed("Thanks for the question — we ship in public.")
    assert allowed
    assert reason == "OK"
    assert cost == X_COST_USD["reply"]


def test_goldenfarfr_handle_not_blocked_as_nfa():
    text = (
        "What do you expect from a holding AI? @solvrbot @grok @aixbt_agent @GoldenFarFR"
    )
    ok, reason = check_tweet_content(text)
    assert ok is True
    assert reason == "OK"


def test_french_tweet_blocked_by_policy():
    text = "Je suis ARIA — nouvelle agente ZHC chez Vanguard."
    ok, reason = check_tweet_content(text)
    assert ok is False
    assert "anglais" in reason.lower()


def test_pump_blocked_with_word_boundary():
    ok, reason = check_tweet_content("Safe text but pump it")
    assert ok is False
    assert "pump" in reason


def test_record_tweet_posted_stores_full_text(tmp_path, monkeypatch):
    ledger_path = tmp_path / "x_api_ledger.json"
    intel_path = tmp_path / "tweet_compose_intel.json"
    monkeypatch.setattr("aria_core.x_publication_policy.LEDGER_PATH", ledger_path)
    monkeypatch.setattr("aria_core.tweet_compose_workflow.INTEL_PATH", intel_path)

    full = "What should a ZHC holding agent optimize first — trust, product, or distribution?"
    record_tweet_posted(full, tweet_id="12345")

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    post = ledger["posts"][0]
    assert post["text"] == full
    assert post["preview"] == full[:120]
    assert post["tweet_id"] == "12345"

    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    assert intel["published_tweets"][0]["text"] == full


def test_list_published_tweets_reads_ledger(tmp_path, monkeypatch):
    ledger_path = tmp_path / "x_api_ledger.json"
    monkeypatch.setattr("aria_core.x_publication_policy.LEDGER_PATH", ledger_path)
    _save_ledger({
        "posts": [
            {"at": "2026-06-19T10:00:00+00:00", "kind": "tweet", "text": "Older tweet?", "tweet_id": "a"},
            {"at": "2026-06-19T12:00:00+00:00", "kind": "tweet", "preview": "Newer preview only", "tweet_id": "b"},
        ],
        "estimated_spend_usd": 0.03,
    })
    posts = list_published_tweets(limit=5)
    assert posts[0]["tweet_id"] == "b"
    assert posts[1]["text"] == "Older tweet?"


def test_platform_15min_cap_blocks(monkeypatch, tmp_path):
    from aria_core import x_publication_policy as mod

    ledger_path = tmp_path / "x_api_ledger.json"
    monkeypatch.setattr(mod, "LEDGER_PATH", ledger_path)
    now = datetime.now(timezone.utc)
    posts = [
        {
            "at": (now - timedelta(minutes=i * 2)).isoformat(),
            "kind": "tweet",
            "text": f"Tweet number {i}",
            "cost_usd": 0.015,
        }
        for i in range(X_SAFE_MAX_POSTS_PER_15MIN)
    ]
    _save_ledger({"posts": posts, "estimated_spend_usd": 0.075})
    allowed, reason, _ = check_tweet_allowed("Another safe tweet for platform test.")
    assert not allowed
    assert "15 min" in reason


def test_duplicate_tweet_blocked_within_48h(monkeypatch, tmp_path):
    from aria_core import x_publication_policy as mod

    ledger_path = tmp_path / "x_api_ledger.json"
    monkeypatch.setattr(mod, "LEDGER_PATH", ledger_path)
    text = "Building in public with Aria Vanguard ZHC on Base."
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _save_ledger({
        "posts": [{"at": old, "kind": "tweet", "text": text, "cost_usd": 0.015}],
        "estimated_spend_usd": 0.015,
    })
    allowed, reason, _ = check_tweet_allowed(text, force=True)
    assert not allowed
    assert "duplicata" in reason.lower() or "identique" in reason.lower()


def test_rate_limit_cooldown_blocks(monkeypatch, tmp_path):
    from aria_core import x_publication_policy as mod

    ledger_path = tmp_path / "x_api_ledger.json"
    monkeypatch.setattr(mod, "LEDGER_PATH", ledger_path)
    record_x_rate_limit(wait_seconds=3600)
    allowed, reason, _ = check_tweet_allowed("Safe tweet after API 429 cooldown.")
    assert not allowed
    assert "429" in reason or "rate limit" in reason.lower()


def test_spend_cap_blocks_when_exceeded(monkeypatch):
    from aria_core import x_publication_policy as mod

    monkeypatch.setattr(mod.settings, "x_monthly_spend_cap_usd", 0.01)
    monkeypatch.setattr(mod.settings, "x_monthly_budget_usd", 5.0)
    monkeypatch.setattr(mod, "_load_ledger", lambda: {"posts": [], "estimated_spend_usd": 0.01})
    allowed, reason, _ = check_tweet_allowed("Safe tweet text here")
    assert not allowed
    assert "Plafond" in reason