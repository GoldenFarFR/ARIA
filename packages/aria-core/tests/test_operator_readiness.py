import pytest

from aria_core.knowledge.web_verify import is_operator_local_question, should_use_web_verify
from aria_core.operator_readiness import (
    parse_readiness_goal,
    wants_operator_go_ahead,
    wants_operator_readiness,
    wants_operator_status_pulse,
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


def test_status_pulse_operateur():
    assert wants_operator_status_pulse("rien de nouveau a declarer ?")
    assert wants_operator_status_pulse("quoi de neuf ?")
    assert not wants_operator_status_pulse("bitcoin aujourd'hui")


def test_operator_local_blocks_web(monkeypatch):
    monkeypatch.setenv("ARIA_PUBLIC_MODE", "false")
    from aria_core.runtime import settings

    settings.aria_public_mode = False
    assert is_operator_local_question("rien de nouveau a declarer ?")
    assert not should_use_web_verify("rien de nouveau a declarer ?")
    assert should_use_web_verify("rugby stade toulousain aujourd'hui")


@pytest.mark.asyncio
async def test_collect_gaps_returns_structure():
    from aria_core.operator_readiness import collect_readiness_gaps

    gaps, ok = await collect_readiness_gaps()
    assert isinstance(gaps, list)
    assert isinstance(ok, list)


@pytest.mark.asyncio
async def test_status_pulse_human_format(monkeypatch, tmp_path):
    from aria_core.memory import collegue as collegue_mod
    from aria_core.operator_readiness import execute_operator_status_pulse

    journal = tmp_path / "JOURNAL.md"
    journal.write_text(
        "20h00 — ancien\n21h00 — milieu\n22h07 — fix pulse\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(collegue_mod, "_ops_memoire_candidates", lambda: [tmp_path])

    async def _fake_gaps(**_):
        return [], ["API locale :8000 OK"]

    monkeypatch.setattr(
        "aria_core.operator_readiness.collect_readiness_gaps",
        _fake_gaps,
    )

    reply, data = await execute_operator_status_pulse("rien de nouveau a declarer ?", lang="fr")
    assert "Rien à déclarer" in reply
    assert "22h07 — fix pulse" in reply
    assert "Structure corporale" not in reply