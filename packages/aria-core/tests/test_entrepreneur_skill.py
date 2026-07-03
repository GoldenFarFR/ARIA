import pytest

from aria_core.brain import detect_intent
from aria_core.models import SkillName
from aria_core.revenue_goals import goal_progress, record_revenue
from aria_core.skills.entrepreneur_skill import execute_entrepreneur, wants_entrepreneur


def test_wants_entrepreneur():
    assert wants_entrepreneur("commence à te cultiver comme entrepreneuse IA")
    assert wants_entrepreneur("objectif 50$ par mois")
    assert wants_entrepreneur("commence à t'activer pour générer des revenus")
    assert wants_entrepreneur("prends des initiatives maintenant")


@pytest.mark.asyncio
async def test_revenue_activation_playbook(monkeypatch, tmp_path):
    from aria_core import revenue_goals as rg

    ledger = tmp_path / "revenue_ledger.json"
    monkeypatch.setattr(rg, "LEDGER_PATH", ledger)
    monkeypatch.setenv("ARIA_ACP_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("ARIA_PROACTIVE_IDEAS", "false")

    out, data = await execute_entrepreneur(
        "tu dois commencer à t'activer pour générer des revenus et prendre des initiatives",
        lang="fr",
    )
    assert data["action"] == "revenue_activation"
    assert "mode revenu ON" in out
    assert "traiter jobs acp" in out
    assert "ARIA_PROACTIVE_IDEAS=OFF" in out


def test_detect_entrepreneur_intent():
    assert detect_intent("commence ta culture entrepreneuse") == SkillName.ENTREPRENEUR_CULTIVATION
    assert detect_intent("revenue goal 50 usd") == SkillName.ENTREPRENEUR_CULTIVATION
    assert detect_intent("étudie Kelly Claude comme pair ZHC") != SkillName.ENTREPRENEUR_CULTIVATION


@pytest.mark.asyncio
async def test_execute_entrepreneur_cultivation(monkeypatch, tmp_path):
    from aria_core import revenue_goals as rg

    ledger = tmp_path / "revenue_ledger.json"
    monkeypatch.setattr(rg, "LEDGER_PATH", ledger)

    out, data = await execute_entrepreneur("commence ta culture entrepreneuse", lang="fr")
    assert "Verdict" in out
    assert "holding" in out.lower() or "Focus" in out
    assert "zhcinstitute" not in out.lower()
    assert "juno" not in out.lower()
    assert "Kelly" in out or "app factory" in out.lower() or "studio apps" in out.lower()
    assert data["action"] == "cultivate"
    assert "goal_monthly_usd" in data["progress"]


@pytest.mark.asyncio
async def test_log_revenue(monkeypatch, tmp_path):
    from aria_core import revenue_goals as rg

    ledger = tmp_path / "revenue_ledger.json"
    monkeypatch.setattr(rg, "LEDGER_PATH", ledger)

    _, data = await execute_entrepreneur("log revenu 25 source gumroad brief", lang="fr")
    assert data["action"] == "log_revenue"
    assert data["entry"]["amount_usd"] == 25.0
    assert goal_progress()["monthly_total_usd"] == 25.0