"""End-to-end proof that AriaBrain.process() clamps free text before ANY
routing regex sees it (backlog #13) -- not just a unit test on
clamp_intent_text in isolation, but the real entry point every Telegram/
public message goes through."""
from __future__ import annotations

import logging
import time

import pytest

from aria_core.brain import AriaBrain


async def _noop_save(*a, **k):
    return None


@pytest.mark.asyncio
async def test_pathological_length_message_is_clamped_before_reaching_llm(monkeypatch, caplog):
    async def _fake_llm(self, message, lang, *, public=False, visitor_id="", extra_system_context=None, **k):
        return "ok"

    monkeypatch.setattr("aria_core.brain.AriaBrain._llm_response", _fake_llm)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)
    monkeypatch.setattr("aria_core.repertoire_db.save_message", _noop_save)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    # Neutral filler -- no greeting/smalltalk/intent keyword anywhere, so this
    # falls through every early interceptor, exercising the SAME routing
    # regexes (_routing_message, detect_intent's INTENT_PATTERNS loop, every
    # wants_* early interceptor) a real oversized/malformed message would.
    pathological = "x " * 60_000  # 120k chars, far past any legitimate message

    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="aria_core.safe_re"):
        response = await AriaBrain().process(pathological, lang="fr", public_mode=False)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0  # ReDoS on the raw text would hang far longer than this
    assert response is not None
    assert "truncated 120000 -> 8192 chars" in caplog.text
