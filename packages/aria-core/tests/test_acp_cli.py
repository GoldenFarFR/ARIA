"""27/08 -- GitHub CodeQL #167 (py/command-line-injection, CWE-088): every
public function in acp_cli.py builds a subprocess argv list from a mix of
hardcoded flag literals and caller-supplied free-text/identifier values.
CodeQL traced a real flow from 3 public /chat endpoints into these functions
(via an LLM tool-call). subprocess.run is never called with shell=True (no
classic shell injection possible), but nothing stopped a value from starting
with ``-`` and being read as a FLAG by acp-cli's own argument parser instead
of the literal value -- these tests confirm the guard (``_safe_value``,
raising ``AcpArgError``) actually fires for every externally-reachable
string field, BEFORE ``run_acp``/subprocess is ever touched, and that a
normal legitimate value still passes through unharmed."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aria_core.skills import acp_cli as m


@pytest.fixture(autouse=True)
def _run_acp_must_not_be_called_by_default(monkeypatch):
    """Guards every test in this file: if a rejection test accidentally lets
    a bad value reach run_acp, this fails loudly instead of silently trying
    to spawn a real subprocess."""
    spy = MagicMock(return_value=(1, "", "run_acp should not have been called"))
    monkeypatch.setattr(m, "run_acp", spy)
    return spy


def test_safe_value_rejects_dash_prefixed_value():
    with pytest.raises(m.AcpArgError):
        m._safe_value("--evil-flag", "field")


def test_safe_value_accepts_normal_value():
    assert m._safe_value("  hello world  ", "field") == "hello world"


def test_safe_value_accepts_empty_string():
    assert m._safe_value("", "field") == ""


def test_schema_arg_rejects_dash_prefixed_raw_string():
    with pytest.raises(m.AcpArgError):
        m._schema_arg("--evil")


def test_schema_arg_dict_never_starts_with_dash():
    # a real dict/list always serializes to "{" or "[", never triggers the guard
    assert m._schema_arg({"a": 1}).startswith("{")


def test_create_offering_rejects_flag_like_name(_run_acp_must_not_be_called_by_default):
    row, err = m.create_offering(name="--rm -rf", description="d", price_value=1.0)
    assert row is None
    assert "name" in err
    _run_acp_must_not_be_called_by_default.assert_not_called()


def test_create_offering_accepts_normal_values(monkeypatch):
    monkeypatch.setattr(m, "run_acp", lambda *a, **kw: (0, '{"id": "1", "name": "ok"}', ""))
    row, err = m.create_offering(name="Real Offering", description="d", price_value=1.0)
    assert err is None
    assert row == {"id": "1", "name": "ok"}


def test_update_offering_rejects_flag_like_offering_id():
    row, err = m.update_offering("--evil", name="x")
    assert row is None
    assert "offering_id" in err


def test_delete_offering_rejects_flag_like_offering_id():
    ok, msg = m.delete_offering("--evil")
    assert ok is False
    assert "offering_id" in msg


def test_job_history_rejects_flag_like_job_id():
    row, err = m.job_history("--evil")
    assert row is None
    assert "job_id" in err


def test_provider_submit_rejects_flag_like_job_id():
    ok, msg = m.provider_submit("--evil", {"a": 1})
    assert ok is False
    assert "job_id" in msg


def test_provider_submit_rejects_flag_like_string_deliverable():
    ok, msg = m.provider_submit("job1", "--evil-payload")
    assert ok is False
    assert "deliverable" in msg


def test_client_create_job_rejects_flag_like_offering_name():
    row, err = m.client_create_job(offering_name="--evil", requirements="r")
    assert row is None
    assert "offering_name" in err


def test_client_fund_job_rejects_flag_like_job_id():
    row, err = m.client_fund_job("--evil")
    assert row is None
    assert "job_id" in err


def test_client_complete_job_rejects_flag_like_reason():
    row, err = m.client_complete_job("job1", reason="--evil")
    assert row is None
    assert "reason" in err


def test_client_reject_job_rejects_flag_like_job_id():
    row, err = m.client_reject_job("--evil")
    assert row is None
    assert "job_id" in err


def test_trade_tokens_rejects_flag_like_token_in():
    row, err = m.trade_tokens(token_in="--evil", token_out="B", amount_in="1")
    assert row is None
    assert "token_in" in err


def test_email_search_rejects_flag_like_query():
    row, err = m.email_search("--evil")
    assert row is None
    assert "query" in err


def test_email_inbox_rejects_flag_like_cursor():
    row, err = m.email_inbox(cursor="--evil")
    assert row is None
    assert "cursor" in err


def test_email_thread_rejects_flag_like_thread_id():
    row, err = m.email_thread("--evil")
    assert row is None
    assert "thread_id" in err


def test_browse_agents_rejects_flag_like_bare_positional_query():
    """The clearest injection vector in the file: query is appended as a bare
    positional argv element with no preceding flag."""
    rows, err = m.browse_agents("--legacy")
    assert rows == []
    assert "query" in err
