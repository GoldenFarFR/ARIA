"""Gem Crush status — grounded answers, no web APK clones."""

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.knowledge.web_verify import (
    fetch_web_snippets,
    is_ecosystem_product_query,
    is_live_info_question,
)
from aria_core.skills.gem_crush_status_skill import (
    answer_gem_crush_status,
    is_gem_crush_ecosystem_question,
)


def test_is_gem_crush_ecosystem_question_matches():
    assert is_gem_crush_ecosystem_question("La prochaine version de gem crush arrive quand ?")
    assert is_gem_crush_ecosystem_question("when is the next ARIA gem crush release")
    assert is_gem_crush_ecosystem_question("match-3 vanguard #poc")


def test_is_gem_crush_ecosystem_question_rejects_generic():
    assert not is_gem_crush_ecosystem_question("quel est le meilleur match-3 mobile ?")
    assert not is_gem_crush_ecosystem_question("prix du bitcoin")


def test_is_ecosystem_product_query_alias():
    assert is_ecosystem_product_query("gem crush version")
    assert not is_ecosystem_product_query("rugby stade toulousain")


def test_is_live_info_false_for_gem_crush():
    assert not is_live_info_question("gem crush prochaine version quand")


@pytest.mark.asyncio
async def test_fetch_web_snippets_skips_ecosystem():
    with patch("aria_core.knowledge.web_verify._fetch_ddg_once", new_callable=AsyncMock) as mock_ddg:
        result = await fetch_web_snippets("gem crush next version")
        assert result == []
        mock_ddg.assert_not_called()


@pytest.mark.asyncio
async def test_answer_gem_crush_status_offline():
    with patch(
        "aria_core.skills.gem_crush_status_skill._fetch_github_status",
        new_callable=AsyncMock,
        return_value={"ok": False},
    ):
        reply = await answer_gem_crush_status(
            "La prochaine version de gem crush arrive quand ?", lang="fr",
        )
    assert "ARIA Gem Crush" in reply
    assert "ariavanguardzhc.com" in reply
    assert "Gem Crush Epic" in reply or "APK" in reply
    assert "Réponse directe" in reply


@pytest.mark.asyncio
async def test_answer_gem_crush_status_with_github():
    with patch(
        "aria_core.skills.gem_crush_status_skill._fetch_github_status",
        new_callable=AsyncMock,
        return_value={
            "ok": True,
            "version": 30,
            "title": "Pack juice v30",
            "updated_at": "2026-06-20",
            "next_version": 31,
            "next_queued": True,
            "next_title": "Premium wave",
            "next_items": 7,
            "repo": "GoldenFarFR/aria-vanguard",
        },
    ):
        reply = await answer_gem_crush_status("gem crush next version", lang="fr")
    assert "v30" in reply
    assert "v31" in reply
    assert "GoldenFarFR/aria-vanguard" in reply