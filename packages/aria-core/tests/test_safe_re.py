"""safe_re.clamp_intent_text -- backlog #13 structural ReDoS fix (06/08):
a single choke point bounding any free text before it reaches intent-
routing regexes, instead of patching each pattern individually."""
from __future__ import annotations

import logging

from aria_core.safe_re import clamp_intent_text


def test_clamp_short_text_passes_through_unchanged():
    assert clamp_intent_text("bonjour") == "bonjour"


def test_clamp_exactly_at_limit_passes_through_unchanged():
    text = "a" * 8192
    assert clamp_intent_text(text) == text


def test_clamp_truncates_beyond_default_limit():
    text = "a" * 10_000
    result = clamp_intent_text(text)
    assert len(result) == 8192
    assert result == "a" * 8192


def test_clamp_respects_custom_max_len():
    text = "a" * 100
    assert clamp_intent_text(text, max_len=10) == "a" * 10


def test_clamp_empty_and_none_round_trip_safely():
    assert clamp_intent_text("") == ""
    assert clamp_intent_text(None) == ""


def test_clamp_logs_a_warning_only_when_truncation_actually_happens(caplog):
    with caplog.at_level(logging.WARNING, logger="aria_core.safe_re"):
        clamp_intent_text("short")
    assert "truncated" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aria_core.safe_re"):
        clamp_intent_text("a" * 9000)
    assert "truncated" in caplog.text
