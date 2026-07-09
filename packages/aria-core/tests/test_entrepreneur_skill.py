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
    assert wants_entrepreneur("mode autonome")
    assert wants_entrepreneur("tu fais ce que tu veux quand tu veux")


@pytest.mark.asyncio
async def test_autonomy_status(monkeypatch, tmp_path):
    from aria_core import autonomy_revenue as ar
    from aria_core import revenue_goals as rg

    monkeypatch.setattr(ar, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(rg, "LEDGER_PATH", tmp_path / "ledger.json")
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_autonomous", True)

    out, data = await execute_entrepreneur("mode autonome", lang="fr")
    assert data["action"] == "autonomy_status"
    assert "AUTONOMIE REVENU" in out


@pytest.mark.asyncio
async def test_revenue_activation_playbook(monkeypatch, tmp_path):
    from aria_core import revenue_goals as rg

    ledger = tmp_path / "revenue_ledger.json"
    monkeypatch.setattr(rg, "LEDGER_PATH", ledger)
    monkeypatch.setenv("ARIA_PROACTIVE_IDEAS", "false")

    out, data = await execute_entrepreneur(
        "tu dois commencer à t'activer pour générer des revenus et prendre des initiatives",
        lang="fr",
    )
    assert data["action"] == "revenue_activation"
    assert "ACP" not in out  # ACP est abandonné, jamais promis en activation
    assert "aucun produit payant" in out.lower()
    assert "protocole-argent-reel.md" in out
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
    assert "Kelly" not in out and "app factory" not in out.lower() and "play store" not in out.lower()
    assert "aucun produit payant" in out.lower()
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