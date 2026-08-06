"""Single choke point for the ReDoS class of bug (backlog #13, 06/08).

The 21 alerts fixed earlier this session (commit 7aff8afe) were all
patch-by-patch: bound the specific vulnerable quantifier in the specific
pattern that CodeQL happened to flag. That closes the KNOWN cases but does
nothing for a pattern nobody has written yet. ``clamp_intent_text`` is the
structural fix instead: every polynomial/exponential regex engine's real
attack surface is INPUT LENGTH -- cap that once, at every entry point that
feeds free text into intent-routing regexes, and the entire bug class stops
mattering regardless of how any individual pattern is written.

Deliberately NOT a ``re`` wrapper (the other half of the original proposal,
cf. the archived Devil's Advocate report on 7aff8afe) -- migrating every
skill off the stdlib ``re`` module is a much larger, higher-risk change for
marginal extra safety once input length is already bounded. This module
covers the high-value, low-risk half.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Telegram itself caps a single message at 4096 chars -- this is roughly
# double that, generous enough that no legitimate operator/public message
# (even a long pasted report) is ever affected, while still keeping any
# regex run against the clamped text firmly in "fast regardless of pattern"
# territory.
_DEFAULT_MAX_LEN = 8192


def clamp_intent_text(text: str, *, max_len: int = _DEFAULT_MAX_LEN) -> str:
    """Truncates ``text`` to ``max_len`` characters -- call this ONCE, at
    the entry point of any free-text routing path, before the text reaches
    ANY regex (existing or future). Never raises, never returns None (a
    falsy/empty input round-trips as-is)."""
    if not text or len(text) <= max_len:
        return text or ""
    logger.warning(
        "safe_re.clamp_intent_text: truncated %d -> %d chars before routing",
        len(text), max_len,
    )
    return text[:max_len]
