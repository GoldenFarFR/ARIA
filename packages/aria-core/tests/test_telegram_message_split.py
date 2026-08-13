"""A message over Telegram's real ~4096-char limit used to be silently
truncated (``_format_tg``'s old ``[:4000]``) -- a real incident found live
(13/08, operator screenshot): a position-tracking alert announced "25
positions ouvertes" in its header but the body showed only 1, with no
indication anything was cut. ``send_message`` now splits any over-limit
text into several complete messages instead. No real network call: the
python-telegram-bot ``Bot`` is faked here, same pattern as
test_heartbeat_trading_topic.py."""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot
from aria_core.runtime import settings


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(
        self, *, chat_id, text, message_thread_id=None, link_preview_options=None,
        parse_mode=None, reply_markup=None,
    ):
        self.calls.append({
            "chat_id": chat_id, "text": text, "message_thread_id": message_thread_id,
            "reply_markup": reply_markup,
        })


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


# ── _split_for_telegram: pure unit tests ────────────────────────────────────


def test_split_short_text_stays_a_single_chunk():
    text = "short message"
    assert telegram_bot._split_for_telegram(text) == [text]


def test_split_long_text_never_exceeds_the_limit_per_chunk():
    lines = [f"line {i} " + ("x" * 100) for i in range(80)]
    text = "\n".join(lines)
    chunks = telegram_bot._split_for_telegram(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= telegram_bot._TELEGRAM_MAX_CHARS


def test_split_never_breaks_a_line_in_half():
    """The real regression: a chart-link URL glued to its position line must
    never be cut mid-URL -- each original line survives whole inside exactly
    one chunk."""
    lines = [f"position {i} https://dexscreener.com/base/0x{'a' * 40}" for i in range(60)]
    text = "\n".join(lines)
    chunks = telegram_bot._split_for_telegram(text)
    rejoined_lines = "\n".join(chunks).split("\n")
    assert rejoined_lines == lines


def test_split_preserves_every_line_no_content_lost():
    header = "🧪 SIMULATION — suivi positions ouvertes (25 positions ouvertes)"
    positions = [f"TOKEN{i} : entry {i}.0" for i in range(25)]
    text = "\n".join([header] + positions)
    chunks = telegram_bot._split_for_telegram(text)
    combined = "\n".join(chunks)
    assert header in combined
    for p in positions:
        assert p in combined


def test_split_pathological_single_line_longer_than_limit_hard_cuts():
    text = "x" * (telegram_bot._TELEGRAM_MAX_CHARS * 2 + 500)
    chunks = telegram_bot._split_for_telegram(text)
    assert len(chunks) >= 2
    assert "".join(chunks) == text


# ── send_message: real split wiring, no more silent truncation ─────────────


@pytest.mark.asyncio
async def test_send_message_splits_an_over_limit_position_tracking_alert(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    header = "🧪 SIMULATION — suivi positions ouvertes (25 positions ouvertes)"
    positions = [
        f"TOKEN{i} (scalping_v9) : {i}.0066 (-2.7%) · P&L latent -803 $ · capital 30,000 $ "
        f"(3.0% du capital de départ) · entrée 1.03427 · détenue 6j8h "
        f"https://dexscreener.com/base/0x{i:040d} · "
        f"https://ops.ariavanguardzhc.com/market?contract=0x{i:040d}&chain=base"
        for i in range(25)
    ]
    long_text = "\n".join([header] + positions)

    ok = await telegram_bot.send_message(long_text, chat_id=-100123)

    assert ok is True
    calls = telegram_bot._bot_app.bot.calls
    assert len(calls) > 1  # never a single silently-truncated message
    combined = "\n".join(c["text"] for c in calls)
    assert header in combined
    for p in positions:
        assert p in combined


@pytest.mark.asyncio
async def test_send_message_short_text_still_sends_a_single_message(monkeypatch):
    """No regression for the overwhelming majority of alerts, well under the limit."""
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    ok = await telegram_bot.send_message("short alert", chat_id=-100123)

    assert ok is True
    assert len(telegram_bot._bot_app.bot.calls) == 1
    assert telegram_bot._bot_app.bot.calls[0]["text"] == "short alert"


@pytest.mark.asyncio
async def test_send_message_reply_markup_only_attached_to_the_last_chunk(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    long_text = "\n".join(f"line {i}" * 100 for i in range(80))
    marker = object()

    await telegram_bot.send_message(long_text, chat_id=-100123, reply_markup=marker)

    calls = telegram_bot._bot_app.bot.calls
    assert len(calls) > 1
    assert all(c["reply_markup"] is None for c in calls[:-1])
    assert calls[-1]["reply_markup"] is marker
