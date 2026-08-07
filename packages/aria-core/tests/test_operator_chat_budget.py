"""07/08 -- explicit operator decision ("bloque toute depense inutile", threshold
"tu met 0.1"): a daily $0.10 spend cap on the operator-chat free-form LLM path
(Telegram admin + mobile), never the public showcase, never trading/analysis
cycles. Unit tests for the state module (`operator_chat_budget.py`) plus the two
real integration points in `brain.py`: the short-circuit in `_general_response`
and the accumulation in `process()`."""
from __future__ import annotations

import json

import pytest

from aria_core import operator_chat_budget
from aria_core.brain import AriaBrain
from aria_core.paths import configure_data_dir


# --- Module state: read/write, fail-open, daily reset -----------------------


def test_no_spend_yet_budget_not_exceeded(tmp_path):
    configure_data_dir(tmp_path)
    assert operator_chat_budget.daily_spent_usd() == 0.0
    assert operator_chat_budget.budget_exceeded() is False


def test_record_spend_accumulates_and_trips_the_cap(tmp_path):
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.04)
    assert operator_chat_budget.daily_spent_usd() == pytest.approx(0.04)
    assert operator_chat_budget.budget_exceeded() is False

    operator_chat_budget.record_spend(0.07)
    assert operator_chat_budget.daily_spent_usd() == pytest.approx(0.11)
    assert operator_chat_budget.budget_exceeded() is True


def test_record_spend_ignores_zero_or_negative(tmp_path):
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.0)
    operator_chat_budget.record_spend(-1.0)
    assert operator_chat_budget.daily_spent_usd() == 0.0


def test_state_persists_on_disk(tmp_path):
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.05)
    state_file = tmp_path / "operator_chat_budget.json"
    assert state_file.exists()
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["spent_usd"] == pytest.approx(0.05)


def test_stale_day_resets_the_counter(tmp_path):
    """A state file left over from a previous UTC day must not carry its spend
    into today -- the whole point of a DAILY cap."""
    configure_data_dir(tmp_path)
    (tmp_path / "operator_chat_budget.json").write_text(
        json.dumps({"day": "2020-01-01", "spent_usd": 999.0}), encoding="utf-8",
    )
    assert operator_chat_budget.daily_spent_usd() == 0.0
    assert operator_chat_budget.budget_exceeded() is False


def test_corrupted_state_file_fails_open(tmp_path):
    """A spend guardrail, not a money guardrail -- a damaged file must never
    brick the operator's ability to talk to ARIA (see outgoing_pause's own
    fail-open doctrine for the same class of file)."""
    configure_data_dir(tmp_path)
    (tmp_path / "operator_chat_budget.json").write_text("not json at all", encoding="utf-8")
    assert operator_chat_budget.daily_spent_usd() == 0.0
    assert operator_chat_budget.budget_exceeded() is False


def test_reply_template_mentions_the_threshold_in_both_languages():
    fr = operator_chat_budget.budget_exceeded_reply("fr")
    en = operator_chat_budget.budget_exceeded_reply("en")
    assert "0.10" in fr
    assert "0.10" in en
    assert fr != en


# --- Integration: _general_response short-circuit ---------------------------


@pytest.mark.asyncio
async def test_operator_free_chat_blocked_once_budget_exceeded(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.10)

    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "ne devrait jamais être atteint"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "Tu baise", "fr", public=False,
    )

    assert llm_calls["n"] == 0
    assert data.get("operator_budget_exceeded") is True
    assert "0.10" in reply


@pytest.mark.asyncio
async def test_operator_free_chat_still_works_under_the_cap(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.05)

    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "réponse libre"

    monkeypatch.setattr("aria_core.brain.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "Tu baise", "fr", public=False,
    )

    assert llm_calls["n"] == 1
    assert data.get("operator_budget_exceeded") is not True


@pytest.mark.asyncio
async def test_public_showcase_chat_never_gated_by_operator_budget(tmp_path, monkeypatch):
    """The cap is scoped to the operator channel only -- a public visitor must
    never see this template, even with the operator budget blown."""
    configure_data_dir(tmp_path)
    operator_chat_budget.record_spend(0.10)

    llm_calls = {"n": 0}

    async def _fake_llm(self, *a, **k):
        llm_calls["n"] += 1
        return "réponse publique"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)

    brain = AriaBrain()
    reply, skill, labels, data, _ = await brain._general_response(
        "hello there", "en", public=True,
    )

    assert data.get("operator_budget_exceeded") is not True


# --- Integration: process() accumulates the real per-turn cost --------------


@pytest.mark.asyncio
async def test_process_accumulates_spend_for_the_operator_channel(tmp_path, monkeypatch):
    from aria_core import llm_usage
    from aria_core.models import ChatResponse

    configure_data_dir(tmp_path)

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        llm_usage.record_llm_usage(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=100_000, output_tokens=10_000,
        )
        return ChatResponse(reply="réponse", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    await brain.process("question test", "fr", public_mode=False)

    expected = (100_000 / 1_000_000) * 1.0 + (10_000 / 1_000_000) * 5.0
    assert operator_chat_budget.daily_spent_usd() == pytest.approx(expected)


@pytest.mark.asyncio
async def test_process_never_accumulates_spend_for_public_visitors(tmp_path, monkeypatch):
    from aria_core import llm_usage
    from aria_core.models import ChatResponse

    configure_data_dir(tmp_path)

    async def fake_process_inner(self, user_message, lang, *, visitor_id="", public_mode=None):
        llm_usage.record_llm_usage(
            provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=100_000, output_tokens=10_000,
        )
        return ChatResponse(reply="réponse", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(AriaBrain, "_process_inner", fake_process_inner)

    brain = AriaBrain()
    await brain.process("question test", "en", public_mode=True)

    assert operator_chat_budget.daily_spent_usd() == 0.0
