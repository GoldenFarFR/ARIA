from aria_core.knowledge.operator_runbook import (
    format_operator_runbook,
    wants_operator_runbook,
)


def test_wants_operator_runbook_triggers():
    assert wants_operator_runbook("runbook")
    assert wants_operator_runbook("nouveau pc")
    assert wants_operator_runbook("nouveau github")
    assert wants_operator_runbook("ne pas oublier les secrets")
    assert wants_operator_runbook("comment setup render")


def test_wants_operator_runbook_negative():
    assert not wants_operator_runbook("What is DEXPulse?")
    assert not wants_operator_runbook("post on X")


def test_format_operator_runbook_fr():
    text = format_operator_runbook("fr")
    assert "Runbook operateur ARIA" in text
    assert "env-sync-no-redeploy" in text
    assert "check-aria-status.ps1" in text
    assert "sync-render.ps1" in text


def test_format_operator_runbook_en():
    text = format_operator_runbook("en")
    assert "operator runbook" in text.lower()
    assert "env-sync-no-redeploy" in text