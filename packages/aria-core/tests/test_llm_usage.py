import json
from datetime import datetime, timezone

from aria_core.cursor_usage import format_cursor_usage_dashboard, update_cursor_usage
from aria_core.llm_usage import (
    format_grok_build_dashboard,
    format_paid_usage_dashboard,
    is_paid_provider,
    parse_usage_from_response,
    paid_usage_snapshot,
    record_llm_usage,
    summarize_grok_build_usage,
    summarize_paid_usage,
    summarize_usage,
)
from aria_core.testing import configure_test_runtime


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


def test_cursor_usage_dashboard(tmp_path, monkeypatch):
    from aria_core import cursor_usage as cu

    monkeypatch.setattr(cu, "cursor_usage_path", lambda: tmp_path / "cursor-usage.json")
    update_cursor_usage(composer_pool_pct=4, api_pool_pct=2, plan="pro+")
    dash = format_cursor_usage_dashboard()
    assert "PRO+" in dash
    assert "Composer 4%" in dash
    assert "API 2%" in dash