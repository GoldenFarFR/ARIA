"""AriaBrain._llm_response -- prompt caching (10/08, operator request: "si sa
aide vraiment a economiser sans detruire la qualite c gratuit"). The stable
persona/identity/channel-rules block must form the PREFIX of the system
prompt (Anthropic's cache only helps a prefix) and its length must be
reported via ``cache_system_prefix_chars`` -- verifies the reorder didn't
drop or duplicate any block, only moved them."""
from __future__ import annotations

import pytest

from aria_core import brain as brain_mod
from aria_core import repertoire_db
from aria_core.locale import LANG_FR


@pytest.fixture(autouse=True)
def _mock_heavy_deps(monkeypatch):
    async def fake_build_llm_context(**kwargs):
        return "CONTEXTE-VARIABLE-DE-CETTE-CONVERSATION"

    async def fake_get_messages(**kwargs):
        return []

    async def fake_get_bot_username():
        return "Aria_ZHC_Bot"

    def fake_get_channel_links_text():
        return "liens factices"

    monkeypatch.setattr(brain_mod, "build_llm_context", fake_build_llm_context)
    monkeypatch.setattr(repertoire_db, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.get_bot_username", fake_get_bot_username
    )
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.get_channel_links_text", fake_get_channel_links_text
    )
    yield


@pytest.mark.asyncio
async def test_standard_chat_reports_a_cache_prefix_length(monkeypatch):
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["system"] = system
        captured["cache_system_prefix_chars"] = kwargs.get("cache_system_prefix_chars")
        return "reponse"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response(
        "explique moi le sizing en detail s'il te plait", LANG_FR, public=False,
    )

    prefix_len = captured["cache_system_prefix_chars"]
    assert prefix_len is not None and prefix_len > 0
    assert prefix_len < len(captured["system"])


@pytest.mark.asyncio
async def test_cache_prefix_is_a_real_prefix_and_excludes_the_variable_context(monkeypatch):
    """The whole point of caching: the stable block must come FIRST, and the
    ever-changing conversation context must be entirely AFTER the cache
    boundary -- otherwise nothing after it is cacheable either."""
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["system"] = system
        captured["cache_system_prefix_chars"] = kwargs.get("cache_system_prefix_chars")
        return "reponse"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response("salut, ca va ?", LANG_FR, public=False)

    system = captured["system"]
    prefix_len = captured["cache_system_prefix_chars"]
    prefix, suffix = system[:prefix_len], system[prefix_len:]
    assert "CONTEXTE-VARIABLE-DE-CETTE-CONVERSATION" not in prefix
    assert "CONTEXTE-VARIABLE-DE-CETTE-CONVERSATION" in suffix


@pytest.mark.asyncio
async def test_reorder_keeps_every_block_present_exactly_once(monkeypatch):
    """Pure reordering, never a content loss -- every distinctive marker
    (persona block, channel rule, the fake context, the language hint) must
    still be present exactly once after the split."""
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["system"] = system
        return "reponse"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response("raconte moi une blague", LANG_FR, public=False)

    system = captured["system"]
    for marker in ("CONTEXTE-VARIABLE-DE-CETTE-CONVERSATION", "Réponds toujours en français", "Public links:"):
        assert system.count(marker) == 1, f"{marker!r} should appear exactly once, found {system.count(marker)}"


@pytest.mark.asyncio
async def test_self_context_path_never_sets_a_cache_prefix(monkeypatch):
    """self_context_only builds its system prompt differently (context-first,
    small/no genuinely stable block) -- unchanged, no caching attempted."""
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["cache_system_prefix_chars"] = kwargs.get("cache_system_prefix_chars")
        return "reponse"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response(
        "qui es-tu", LANG_FR, public=False, self_context_only=True,
    )

    assert captured["cache_system_prefix_chars"] is None
