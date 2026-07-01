import pytest

from aria_core.member_memory import (
    build_member_greeting,
    get_member_profile,
    member_visitor_id,
    remember_fact,
    touch_member,
)


@pytest.mark.asyncio
async def test_member_touch_and_greeting():
    did = "did:privy:mem-test-1"
    p1 = await touch_member(privy_did=did, handle="player1", site_slug="kikou")
    assert p1.visit_count == 1
    assert p1.handle == "player1"
    greeting1 = await build_member_greeting(p1, site_slug="kikou", game_id="2048")
    assert "player1" in greeting1
    assert "première" in greeting1.lower() or "premiere" in greeting1.lower()

    p2 = await touch_member(privy_did=did, handle="player1", site_slug="kikou")
    assert p2.visit_count == 2
    greeting2 = await build_member_greeting(p2, site_slug="kikou", game_id="2048")
    assert "visite" in greeting2.lower()

    await remember_fact(did, "nickname", "Champion")
    loaded = await get_member_profile(did)
    assert loaded is not None
    assert loaded.facts.get("nickname") == "Champion"
    assert member_visitor_id(did) == f"member:{did}"