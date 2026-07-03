"""Tests réponses instinctives ouvrier."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ouvrier_instant import instant_reply, is_simple_exchange

HERE = Path(__file__).resolve().parent


def test_simple_greeting_wellbeing():
    msg = "salut aria la forme aujourd'hui ?"
    assert is_simple_exchange(msg)
    reply = instant_reply(msg)
    assert "Salut" in reply
    assert "?" in reply
    assert "Vanguard" not in reply
    assert "status" not in reply.lower()


def test_simple_salut_only():
    assert is_simple_exchange("salut")
    assert instant_reply("salut") == "Salut Sylvain !"


def test_not_simple_acp():
    msg = "créer workflow acp template veille_zhc_x1"
    assert not is_simple_exchange(msg)


def test_not_simple_opinion_acp():
    msg = "tu a creer un nouveau workflow sur acp j'ai vu tu en pense quoi ?"
    assert not is_simple_exchange(msg)


def test_not_simple_continuation():
    assert not is_simple_exchange("daccord regarde")
    assert not is_simple_exchange("ok vas-y")


def test_enrich_continuation_with_session(tmp_path, monkeypatch):
    from ouvrier_session import enrich_continuation, save_session

    monkeypatch.setattr(
        "ouvrier_session._SESSION_PATH",
        tmp_path / "kart-session.json",
    )
    save_session(
        "tu a creer un nouveau workflow sur acp tu en pense quoi ?",
        "Le workflow lie offering + promo X.",
    )
    enriched = enrich_continuation("daccord regarde")
    assert "workflow sur acp" in enriched
    assert "Suite" in enriched


def test_preflight_acp_injects_repo():
    from orchestrate_ouvrier import preflight_acp_context

    block = preflight_acp_context("tu en penses quoi du nouveau workflow acp ?")
    assert "acp_product_launch_skill" in block
    assert "acp_offerings.yaml" in block
    assert "TON avis" in block or "avis concret" in block


def test_preflight_acp_skips_create():
    from orchestrate_ouvrier import preflight_acp_context

    block = preflight_acp_context(
        "cree un workflow appeler test_1 a 25$ sur acp qui propose analyse quantitative"
    )
    assert block == ""


def test_ouvrier_acp_direct_delete(monkeypatch):
    from ouvrier_acp_direct import try_acp_workflow_direct

    async def fake_delete(msg, lang):
        return "C'est fait — workflow test_1 supprimé sur ACP (ID oid-del).", {"acp": "offering_delete"}

    monkeypatch.setattr(
        "aria_core.skills.acp_offering_skill.execute_offering_delete",
        fake_delete,
    )
    monkeypatch.setattr(
        "aria_core.skills.acp_offering_skill.wants_acp_offering_delete",
        lambda m: "workflow test" in m.lower(),
    )
    monkeypatch.setattr(
        "aria_core.skills.acp_offering_skill.wants_adhoc_acp_workflow",
        lambda m: False,
    )
    reply = try_acp_workflow_direct("supprime le workflow test 1 maintenant")
    assert reply
    assert "test_1" in reply
    assert "supprimé" in reply.lower()


def test_ouvrier_acp_direct_short_reply(monkeypatch):
    from ouvrier_acp_direct import try_acp_workflow_direct

    async def fake_exec(msg, lang):
        return "C'est fait — workflow test_1 créé sur ACP.\n25.99 USDC · SLA 60m · ID abc", {}

    monkeypatch.setattr(
        "aria_core.skills.acp_offering_skill.execute_adhoc_workflow_create",
        fake_exec,
    )
    monkeypatch.setattr(
        "aria_core.skills.acp_offering_skill.wants_adhoc_acp_workflow",
        lambda m: "appeler test_1" in m,
    )
    reply = try_acp_workflow_direct(
        "cree un workflow appeler test_1 a 25$ et 99 centimes sur acp qui propose analyse"
    )
    assert reply
    assert "test_1" in reply
    assert len(reply) < 300


def test_not_simple_worker():
    assert not is_simple_exchange("traite les pending aria-worker")


def test_merci():
    assert is_simple_exchange("merci !")
    assert "plaisir" in instant_reply("merci !").lower()


def test_cli_instant_no_trace():
    proc = subprocess.run(
        [sys.executable, str(HERE / "orchestrate_ouvrier.py"), "--message", "salut aria la forme ?"],
        cwd=str(HERE),
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0
    out = (proc.stdout or "").strip()
    assert "Salut" in out
    assert "[pensee]" not in (proc.stdout or "") + (proc.stderr or "")
    assert "handoff" not in out.lower()
    assert "moteur" not in (proc.stderr or "").lower()