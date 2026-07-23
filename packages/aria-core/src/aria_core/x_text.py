"""X/Twitter text limits — weighted counting (URLs, emoji) + safe truncation, API v2."""

from __future__ import annotations

import re
import unicodedata

# X API v2 — standard tweet (outside the long-form subscription)
X_TWEET_MAX_CHARS = 280
X_URL_WEIGHTED_CHARS = 23

# Vanguard site — full review recorded; the tweet quotes an auto-adapted excerpt
FEEDBACK_SITE_MAX_CHARS = 500
FEEDBACK_X_QUOTE_MAX_WEIGHT = 200
# Single tweet (thread) — longer quote, reply as a reply
FEEDBACK_X_QUOTE_THREAD_MAX_WEIGHT = 255
# Target fill of feedback tweets (readability + density)
FEEDBACK_X_MIN_TWEET_FILL_RATIO = 0.70


def feedback_x_min_tweet_weight(max_chars: int = X_TWEET_MAX_CHARS) -> int:
    return int(max_chars * FEEDBACK_X_MIN_TWEET_FILL_RATIO)

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    if cp == 0:
        return 0
    cat = unicodedata.category(ch)
    if cp > 0xFFFF or cat in ("So", "Sk"):
        return 2
    if cat == "Mn":
        return 0
    return 1


def weighted_tweet_length(text: str) -> int:
    """"X weight" length: URLs count as 23, most emoji count double."""
    if not text:
        return 0
    total = 0
    last = 0
    for match in _URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[last : match.start()])
        total += X_URL_WEIGHTED_CHARS
        last = match.end()
    total += sum(_char_weight(c) for c in text[last:])
    return total


def tweet_fits(text: str, max_chars: int = X_TWEET_MAX_CHARS) -> bool:
    return weighted_tweet_length(text) <= max_chars


def fit_x_tweet(text: str, max_chars: int = X_TWEET_MAX_CHARS, ellipsis: str = "…") -> str:
    """Truncates without exceeding the X weight or brutally cutting UTF-8."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if tweet_fits(raw, max_chars):
        return raw

    ell_weight = weighted_tweet_length(ellipsis)
    budget = max(1, max_chars - ell_weight)

    lo, hi = 0, len(raw)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = raw[:mid].rstrip()
        if weighted_tweet_length(candidate) <= budget:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1

    if not best:
        return fit_x_tweet(ellipsis, max_chars=max_chars, ellipsis="")

    if hi < len(raw) and best and raw[hi : hi + 1].isalnum():
        sp = best.rfind(" ")
        if sp > max(8, len(best) // 3):
            best = best[:sp].rstrip()

    out = f"{best}{ellipsis}" if best else ellipsis
    return fit_x_tweet(out, max_chars=max_chars, ellipsis="") if not tweet_fits(out, max_chars) else out