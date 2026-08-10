"""x402 payment attempt journal -- safety net independent of SQLite (10/08,
real incident: a legitimate twit.sh x402 payment settled on-chain with zero
trace in x402_spend_log, root cause never fully pinned down but the
structural fix doesn't depend on knowing it: this journal, cf.
x402_attempt_journal.py's own module docstring)."""
from __future__ import annotations

import json

from aria_core import x402_attempt_journal as journal


def test_record_attempt_writes_one_json_line(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "data_dir", lambda: tmp_path)

    journal.record_attempt(
        resource="tweets-user", provider="twitsh", amount_usd=0.01,
        pay_to="0x9dBA414637c611a16BEa6f0796BFcbcBdc410df8",
        contract="", token_symbol="",
    )

    path = tmp_path / "x402_payment_attempts.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["resource"] == "tweets-user"
    assert row["provider"] == "twitsh"
    assert row["amount_usd"] == 0.01
    assert row["pay_to"] == "0x9dBA414637c611a16BEa6f0796BFcbcBdc410df8"
    assert row["ts"]


def test_record_attempt_appends_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "data_dir", lambda: tmp_path)

    journal.record_attempt(resource="a", provider="p1", amount_usd=0.01, pay_to="0xaaa")
    journal.record_attempt(resource="b", provider="p2", amount_usd=0.02, pay_to="0xbbb")

    path = tmp_path / "x402_payment_attempts.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_record_attempt_never_raises_on_write_failure(tmp_path, monkeypatch):
    # data_dir() returns a path that can't be written to (points at a file,
    # not a directory) -- record_attempt must swallow this, never propagate,
    # since it sits directly in the payment path.
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("x")
    monkeypatch.setattr(journal, "data_dir", lambda: blocked)

    journal.record_attempt(resource="a", provider="p1", amount_usd=0.01, pay_to="0xaaa")  # must not raise


def test_recent_attempts_empty_when_journal_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "data_dir", lambda: tmp_path)
    assert journal.recent_attempts() == []


def test_recent_attempts_filters_by_window(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(journal, "data_dir", lambda: tmp_path)
    now = datetime.now(timezone.utc)
    path = tmp_path / "x402_payment_attempts.jsonl"
    old_row = {"ts": (now - timedelta(hours=2)).isoformat(), "resource": "old", "provider": "p", "amount_usd": 0.01, "pay_to": "0xaaa"}
    recent_row = {"ts": (now - timedelta(minutes=5)).isoformat(), "resource": "recent", "provider": "p", "amount_usd": 0.01, "pay_to": "0xaaa"}
    path.write_text(json.dumps(old_row) + "\n" + json.dumps(recent_row) + "\n", encoding="utf-8")

    out = journal.recent_attempts(since=now - timedelta(minutes=30))
    assert len(out) == 1
    assert out[0]["resource"] == "recent"


def test_recent_attempts_never_raises_on_corrupted_line(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "data_dir", lambda: tmp_path)
    path = tmp_path / "x402_payment_attempts.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    assert journal.recent_attempts() == []
