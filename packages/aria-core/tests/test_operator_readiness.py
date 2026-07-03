import pytest

from aria_core.operator_readiness import (
    parse_readiness_goal,
    wants_operator_go_ahead,
    wants_operator_readiness,
)


def test_readiness_phrase_operateur():
    msg = (
        "ok et maintenant tout est pret, qu'est-ce qu'il manque "
        "pour que tu puisses publier sur le site"
    )
    assert wants_operator_readiness(msg)
    assert "publier sur le site" in parse_readiness_goal(msg)


def test_go_ahead_benefique():
    assert wants_operator_go_ahead("si c'est benefique pour toi fait le")


def test_not_readiness_random():
    assert not wants_operator_readiness("comment va le marché crypto ?")


@pytest.mark.asyncio
async def test_collect_gaps_returns_structure():
    from aria_core.operator_readiness import collect_readiness_gaps

    gaps, ok = await collect_readiness_gaps()
    assert isinstance(gaps, list)
    assert isinstance(ok, list)