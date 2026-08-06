import json
from datetime import datetime, timezone

import pytest

from aria_core.cursor_usage import format_cursor_usage_dashboard, update_cursor_usage
from aria_core.llm_usage import (
    begin_chat_usage_tracking,
    clear_chat_usage_tracking,
    clear_monthly_cost_cache,
    cost_usd_for,
    format_grok_build_dashboard,
    format_paid_usage_dashboard,
    get_chat_fallback_state,
    get_chat_usage_totals,
    is_paid_provider,
    mark_fallback_used,
    monthly_cost_usd,
    parse_usage_from_response,
    paid_usage_snapshot,
    record_llm_usage,
    summarize_grok_build_usage,
    summarize_paid_usage,
    summarize_usage,
)
from aria_core.testing import configure_test_runtime


@pytest.fixture(autouse=True)
def _clear_monthly_cost_cache():
    """The cache is a module-level dict shared across the whole test
    process -- without this, two tests reusing the same month literal
    (several do, "2026-07") would leak a stale value between them."""
    clear_monthly_cost_cache()
    yield
    clear_monthly_cost_cache()


def test_parse_usage_from_response_openai_shape():
    data = {"usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
    assert parse_usage_from_response(data) == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }


def test_record_and_summarize_usage(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    record_llm_usage(
        provider="grok",
        model="grok-4.3",
        input_tokens=6000,
        output_tokens=900,
        at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )
    record_llm_usage(
        provider="groq",
        model="llama-3.3-70b-versatile",
        input_tokens=5000,
        output_tokens=500,
        ok=False,
        status_code=429,
        at=datetime(2026, 7, 3, 13, 0, tzinfo=timezone.utc),
    )
    summary = summarize_usage(month="2026-07")
    assert summary["totals"]["input_tokens"] == 6000
    assert summary["totals"]["output_tokens"] == 900
    assert summary["totals"]["total_tokens"] == 6900
    assert summary["totals"]["calls_ok"] == 1
    assert summary["totals"]["calls_failed"] == 1
    assert summary["by_provider"]["grok"]["total_tokens"] == 6900
    assert summary["by_day"]["2026-07-03"]["calls"] == 1

    log = tmp_path / "llm-usage" / "2026-07.jsonl"
    assert log.is_file()
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["provider"] == "grok"


def test_paid_usage_excludes_ollama(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    record_llm_usage(
        provider="grok",
        model="grok-4.3",
        input_tokens=1000,
        output_tokens=200,
        at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )
    record_llm_usage(
        provider="ollama",
        model="qwen2.5:14b",
        input_tokens=5000,
        output_tokens=500,
        at=datetime(2026, 7, 3, 12, 5, tzinfo=timezone.utc),
    )
    paid = summarize_paid_usage(month="2026-07")
    assert paid["totals"]["total_tokens"] == 1200
    assert is_paid_provider("grok")
    assert not is_paid_provider("ollama")
    snap = paid_usage_snapshot(month="2026-07")
    assert snap["month_total_tokens"] == 1200
    assert snap["lifetime_total_tokens"] == 1200
    dash = format_paid_usage_dashboard(month="2026-07")
    assert "grok 2026-07" in dash
    assert "total:" in dash


def test_grok_build_usage_excludes_groq(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    record_llm_usage(
        provider="grok",
        model="grok-4.3",
        input_tokens=800,
        output_tokens=200,
        at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )
    record_llm_usage(
        provider="groq",
        model="llama-3.3-70b",
        input_tokens=4000,
        output_tokens=400,
        at=datetime(2026, 7, 3, 12, 1, tzinfo=timezone.utc),
    )
    grok = summarize_grok_build_usage(month="2026-07")
    assert grok["totals"]["total_tokens"] == 1000
    dash = format_grok_build_dashboard(month="2026-07")
    assert dash.startswith("grok 2026-07:")


def test_chat_fallback_state_noop_outside_tracked_turn():
    """#135 : hors d'un tour suivi (pas de begin_chat_usage_tracking actif), mark_fallback_used
    ne doit rien faire -- même patron de no-op que _accumulate_chat_usage."""
    clear_chat_usage_tracking()
    mark_fallback_used("groq")
    assert get_chat_fallback_state() == {"used": False, "provider": ""}


def test_chat_fallback_state_tracked_turn():
    begin_chat_usage_tracking()
    try:
        assert get_chat_fallback_state() == {"used": False, "provider": ""}
        mark_fallback_used("groq")
        assert get_chat_fallback_state() == {"used": True, "provider": "groq"}
    finally:
        clear_chat_usage_tracking()
    # Après clear, retour à l'état neutre.
    assert get_chat_fallback_state() == {"used": False, "provider": ""}


# ── Cost tracking (06/08, operator request: "XXX$ dépensé" on every paid ──
# Telegram reply, Haiku/Sonnet only). Prices sourced live 06/08 (anthropic.com):
# Haiku 4.5 = $1/$5 per million; Sonnet 5 = $2/$10 through 2026-08-31, $3/$15
# from 2026-09-01. These tests lock the exact numbers AND the step-up date --
# a silent drift here means every operator-facing cost figure goes wrong.

def test_haiku_cost_known_and_correct():
    cost = cost_usd_for(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cost == 6.0  # $1 in + $5 out


def test_sonnet_cost_intro_price_before_step_up():
    cost = cost_usd_for(
        provider="anthropic", model="claude-sonnet-5",
        input_tokens=1_000_000, output_tokens=1_000_000,
        at=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
    )
    assert cost == 12.0  # $2 in + $10 out, introductory price


def test_sonnet_cost_standard_price_after_step_up():
    cost = cost_usd_for(
        provider="anthropic", model="claude-sonnet-5",
        input_tokens=1_000_000, output_tokens=1_000_000,
        at=datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert cost == 18.0  # $3 in + $15 out, standard price


def test_unknown_model_cost_is_none_never_guessed():
    assert cost_usd_for(
        provider="grok", model="x-ai-grok-4-3", input_tokens=1000, output_tokens=1000,
    ) is None
    assert cost_usd_for(
        provider="anthropic", model="claude-opus-4-8", input_tokens=1000, output_tokens=1000,
    ) is None  # not in the price table -- honest degradation, not a guess


def test_record_llm_usage_persists_cost_usd(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    record_llm_usage(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        input_tokens=100_000,
        output_tokens=10_000,
        at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )
    month_cost = monthly_cost_usd(month="2026-07")
    assert month_cost == pytest.approx((100_000 / 1_000_000) * 1.0 + (10_000 / 1_000_000) * 5.0)


def test_monthly_cost_usd_ignores_unpriced_providers(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    record_llm_usage(
        provider="grok", model="x-ai-grok-4-3", input_tokens=1_000_000, output_tokens=1_000_000,
        at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert monthly_cost_usd(month="2026-07") == 0.0


def test_chat_usage_tracking_accumulates_real_cost():
    begin_chat_usage_tracking()
    try:
        record_llm_usage(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
        totals = get_chat_usage_totals()
        assert totals["cost_usd"] == 6.0
        assert totals["cost_unknown"] is False
    finally:
        clear_chat_usage_tracking()


def test_chat_usage_tracking_flags_unknown_cost():
    begin_chat_usage_tracking()
    try:
        record_llm_usage(provider="grok", model="x-ai-grok-4-3", input_tokens=1000, output_tokens=100)
        totals = get_chat_usage_totals()
        assert totals["cost_usd"] == 0.0
        assert totals["cost_unknown"] is True
    finally:
        clear_chat_usage_tracking()


def test_cursor_usage_dashboard(tmp_path, monkeypatch):
    from aria_core import cursor_usage as cu

    monkeypatch.setattr(cu, "cursor_usage_path", lambda: tmp_path / "cursor-usage.json")
    update_cursor_usage(composer_pool_pct=4, api_pool_pct=2, plan="pro+")
    dash = format_cursor_usage_dashboard()
    assert "PRO+" in dash
    assert "Composer 4%" in dash
    assert "API 2%" in dash