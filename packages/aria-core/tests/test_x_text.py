from aria_core.x_text import (
    FEEDBACK_SITE_MAX_CHARS,
    X_TWEET_MAX_CHARS,
    fit_x_tweet,
    tweet_fits,
    weighted_tweet_length,
)


def test_weighted_url_counts_as_23():
    text = "Check https://ariavanguardzhc.com/page today"
    assert weighted_tweet_length(text) < len(text)


def test_emoji_counts_heavier_than_ascii():
    plain = "a" * 280
    emoji = "😀" * 140
    assert weighted_tweet_length(plain) == 280
    assert weighted_tweet_length(emoji) == 280
    assert tweet_fits(plain)
    assert not tweet_fits(plain + "x")
    assert not tweet_fits(emoji + "😀")


def test_fit_x_tweet_never_exceeds_max():
    long = "Word " * 120
    fitted = fit_x_tweet(long)
    assert tweet_fits(fitted)
    assert fitted.endswith("…")
    assert len(fitted) <= X_TWEET_MAX_CHARS + 5


def test_feedback_site_max_sane():
    assert FEEDBACK_SITE_MAX_CHARS >= 280
    assert FEEDBACK_SITE_MAX_CHARS <= 500