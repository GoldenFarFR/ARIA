"""Public site widget LLM kill switch (10/08, operator: "desactive tout llm sur
la vitrine je veut pas consommer de token inutilement" -- after finding the
`resolve_budget(grounded=True)` branch silently bypassed the Anthropic routing
gate and burned Grok/Groq calls at an 85% failure rate).

Single choke point: llm.chat_with_context reads a contextvar set once per
request in brain._process_inner (llm_economy.set_public_llm_context) --
verified at both ends here rather than chasing every public-reachable
chat_with_context call site individually."""
from __future__ import annotations

import pytest

from aria_core import llm as llm_mod
from aria_core import llm_economy
from aria_core.runtime import get_settings


@pytest.fixture(autouse=True)
def _reset_context():
    llm_economy._public_llm_disabled.set(False)
    settings = get_settings()
    settings.aria_vitrine_llm_enabled = False
    yield
    llm_economy._public_llm_disabled.set(False)
    settings.aria_vitrine_llm_enabled = False


@pytest.mark.asyncio
async def test_chat_with_context_short_circuits_for_public_when_gate_off(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("chat_with_context must never reach the network for public traffic")

    monkeypatch.setattr(llm_mod, "_post_chat", fail_if_called)
    llm_economy.set_public_llm_context(True)

    out = await llm_mod.chat_with_context("salut", "sys", max_tokens=50)

    assert out is None


@pytest.mark.asyncio
async def test_chat_with_context_unaffected_for_operator_traffic(monkeypatch):
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"

    async def fake_post(route, **kwargs):
        return "reponse-reelle"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    llm_economy.set_public_llm_context(False)  # operator, not a visitor

    out = await llm_mod.chat_with_context("salut", "sys", max_tokens=50)

    assert out == "reponse-reelle"


def test_set_public_llm_context_only_disables_for_public():
    llm_economy.set_public_llm_context(False)
    assert llm_economy.is_public_llm_disabled_now() is False

    llm_economy.set_public_llm_context(True)
    assert llm_economy.is_public_llm_disabled_now() is True


def test_re_enabling_the_gate_restores_normal_public_behavior():
    settings = get_settings()
    settings.aria_vitrine_llm_enabled = True

    llm_economy.set_public_llm_context(True)

    assert llm_economy.is_public_llm_disabled_now() is False


@pytest.mark.asyncio
async def test_brain_process_inner_wires_the_real_public_flag(monkeypatch):
    from aria_core import brain as brain_mod
    from aria_core import repertoire_db

    captured = {}

    def fake_set_public_llm_context(public):
        captured["public"] = public

    async def fake_save_message(*args, **kwargs):
        return None

    monkeypatch.setattr(brain_mod, "clamp_intent_text", lambda m: m)
    monkeypatch.setattr(llm_economy, "set_public_llm_context", fake_set_public_llm_context)
    monkeypatch.setattr(brain_mod, "is_public_mode", lambda: False)
    monkeypatch.setattr(repertoire_db, "save_message", fake_save_message)

    try:
        await brain_mod.aria_brain._process_inner("salut", "fr", public_mode=True)
    except Exception:
        pass  # downstream routing may fail on unmocked deps -- irrelevant here

    assert captured.get("public") is True
