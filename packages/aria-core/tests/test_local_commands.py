import pytest

from aria_core.gateway.local_commands import (
    execute_local_command,
    is_local_command,
    parse_local_command,
)


def test_parse_local_command():
    assert parse_local_command("/directive test rule") == ("directive", "test rule")
    assert parse_local_command("/help") == ("help", "")
    assert parse_local_command("acp status") is None


def test_parse_natural_local_command():
    assert parse_local_command("état aria") == ("status", "")
    assert parse_local_command("comment vas-tu") == ("status", "")
    assert parse_local_command("montre qi aria") == ("qi", "")
    assert parse_local_command("aide aria") == ("help", "")
    assert parse_local_command("directive : toujours Spark") == ("directive", "toujours Spark")
    assert parse_local_command("apprends : acp | scan hebdo") == ("learn", "acp | scan hebdo")


def test_is_local_command():
    assert is_local_command("/qi")
    assert is_local_command("état aria")
    assert not is_local_command("scan marché acp")


@pytest.mark.asyncio
async def test_local_help():
    out, data = await execute_local_command("/help", lang="fr")
    assert data["local_command"] == "help"
    assert "directive" in out.lower()
    assert "langage naturel" in out.lower()


@pytest.mark.asyncio
async def test_local_directive(tmp_path, monkeypatch):
    from aria_core import directives as dmod

    op_path = tmp_path / "directives" / "operator.md"
    op_path.parent.mkdir(parents=True)
    op_path.write_text("# test\n", encoding="utf-8")
    monkeypatch.setattr(dmod, "operator_directives_path", lambda: op_path)

    out, data = await execute_local_command(
        "/directive Chaque scan ACP propose 1 workflow concret",
        lang="fr",
    )
    assert data.get("ok") is True
    assert "enregistrée" in out.lower() or "saved" in out.lower()
    assert "workflow" in op_path.read_text(encoding="utf-8")