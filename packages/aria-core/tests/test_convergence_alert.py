"""convergence_alert.format_convergence_alert -- Telegram text for a 2/3 or
3/3 brain_correlation crossing. Pure text formatting, no I/O.

Never a trade verdict: the whole point of this alert is to prove the three
brains CAN meet temporally, not to recommend an action."""
from __future__ import annotations

from aria_core.convergence_alert import format_convergence_alert


def _state(level, brains, observed_at):
    return {
        "level": level, "brains_positive": brains, "count": len(brains),
        "observed_at": observed_at,
    }


def test_2_of_3_uses_alarm_emoji():
    state = _state("2/3", ["on_chain", "social"], {
        "on_chain": "2026-09-03T12:00:02+00:00", "social": "2026-09-03T12:00:11+00:00",
    })
    text = format_convergence_alert("FOO", chain="robinhood", state=state)
    assert "🚨" in text
    assert "2/3" in text
    assert "🔥" not in text


def test_3_of_3_uses_fire_emoji():
    state = _state("3/3", ["on_chain", "social", "chart"], {
        "on_chain": "2026-09-03T12:00:02+00:00",
        "social": "2026-09-03T12:00:11+00:00",
        "chart": "2026-09-03T12:00:47+00:00",
    })
    text = format_convergence_alert("FOO", chain="robinhood", state=state)
    assert "🔥" in text
    assert "3/3" in text


def test_positive_brains_marked_green_missing_brain_never_fabricated():
    state = _state("2/3", ["on_chain", "social"], {
        "on_chain": "2026-09-03T12:00:02+00:00", "social": "2026-09-03T12:00:11+00:00",
    })
    text = format_convergence_alert("FOO", chain="robinhood", state=state)
    assert "ON-CHAIN 🟢" in text
    assert "SOCIAL 🟢" in text
    # chart never fired -- must render as pending, never a fabricated red/green
    assert "CHART" in text
    chart_line = [l for l in text.splitlines() if l.strip().startswith("CHART")][0]
    assert "🟢" not in chart_line
    assert "🔴" not in chart_line


def test_never_a_trade_verdict():
    state = _state("2/3", ["on_chain", "social"], {
        "on_chain": "2026-09-03T12:00:02+00:00", "social": "2026-09-03T12:00:11+00:00",
    })
    text = format_convergence_alert("FOO", chain="robinhood", state=state)
    for forbidden in ("BUY", "SELL", "ENTRY", "ACHAT", "VENTE"):
        assert forbidden not in text


def test_symbol_none_never_fabricated():
    state = _state("2/3", ["on_chain", "social"], {
        "on_chain": "2026-09-03T12:00:02+00:00", "social": "2026-09-03T12:00:11+00:00",
    })
    text = format_convergence_alert(None, chain="robinhood", state=state)
    assert "?" in text


def test_timestamps_rendered_for_positive_brains():
    state = _state("2/3", ["on_chain", "social"], {
        "on_chain": "2026-09-03T12:00:02+00:00", "social": "2026-09-03T12:00:11+00:00",
    })
    text = format_convergence_alert("FOO", chain="robinhood", state=state)
    assert "12:00:02" in text
    assert "12:00:11" in text
