import json
from datetime import datetime, timezone

from aria_core.llm_usage import (
    parse_usage_from_response,
    record_llm_usage,
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