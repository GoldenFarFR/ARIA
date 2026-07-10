import pytest

from aria_core.gateway.local_commands import (
    execute_local_command,
    is_local_command,
    parse_local_command,
)


def test_parse_local_command():
    assert parse_local_command("/help") == ("help", "")
    assert parse_local_command("acp status") is None


def test_parse_natural_local_command():
    assert parse_local_command("état aria") == ("status", "")
    assert parse_local_command("comment vas-tu") == ("status", "")
    assert parse_local_command("montre qi aria") == ("qi", "")
    assert parse_local_command("aide aria") == ("help", "")
    assert parse_local_command("apprends : acp | scan hebdo") == ("learn", "acp | scan hebdo")


def test_directive_no_longer_a_local_command():
    # Retire le 10/07 (jamais utilise en pratique, doublon du vrai flux : demander
    # a Claude Code d'editer directives.md directement, revu et teste).
    assert parse_local_command("/directive test rule") is None
    assert parse_local_command("directive : toujours Spark") is None


def test_is_local_command():
    assert is_local_command("/qi")
    assert is_local_command("état aria")
    assert not is_local_command("scan marché acp")


@pytest.mark.asyncio
async def test_local_help():
    out, data = await execute_local_command("/help", lang="fr")
    assert data["local_command"] == "help"
    assert "langage naturel" in out.lower()