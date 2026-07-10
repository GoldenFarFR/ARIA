"""Canal de directives ARIA -> Claude Code (pilote, DB + marqueur isoles).

Verrouille le bordage : perimetre en dur, gate OFF par defaut, coupe-circuit dedie,
journal append-only. Aucune execution reelle ici -- ce module ne fait qu'une file.
"""
from __future__ import annotations

import pytest

from aria_core import aria_directives as ad


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "DB_PATH", str(tmp_path / "directives.db"))
    # data_dir() sert au marqueur de coupe-circuit : on l'isole aussi.
    monkeypatch.setattr(ad, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", "1")
    yield


@pytest.mark.asyncio
async def test_gate_off_refuses_proposal(monkeypatch):
    monkeypatch.delenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", raising=False)
    assert ad.channel_enabled() is False
    res = await ad.propose_directive("docs", "mettre a jour un doc")
    assert res["ok"] is False
    assert "desactive" in res["reason"]
    assert await ad.list_directives() == []


@pytest.mark.asyncio
async def test_out_of_perimeter_category_is_refused_and_logged():
    res = await ad.propose_directive("wallet", "signer une transaction")
    assert res["ok"] is False
    assert "hors perimetre" in res["reason"]
    # Rien dans la file, mais la tentative est tracee dans le journal.
    assert await ad.list_directives() == []
    log = await ad.read_log()
    assert any(e["event"] == "refused" and "wallet" in e["detail"] for e in log)


@pytest.mark.asyncio
async def test_allowed_category_enters_queue_pending():
    res = await ad.propose_directive("repo_hygiene", "retirer du code mort", "detail ...")
    assert res["ok"] is True
    pending = await ad.list_directives(status="pending")
    assert len(pending) == 1
    assert pending[0]["title"] == "retirer du code mort"
    assert pending[0]["category"] == "repo_hygiene"


@pytest.mark.asyncio
async def test_full_lifecycle_claim_then_complete():
    await ad.propose_directive("backlog", "ajouter une tache")
    claimed = await ad.claim_next_directive()
    assert claimed is not None and claimed["title"] == "ajouter une tache"
    assert (await ad.list_directives(status="executing"))
    done = await ad.complete_directive(claimed["id"], outcome="fait, commit abc123")
    assert done["status"] == "done"
    log_events = [e["event"] for e in await ad.read_log()]
    assert {"proposed", "claimed", "executed"} <= set(log_events)


@pytest.mark.asyncio
async def test_claim_returns_none_when_halted():
    await ad.propose_directive("docs", "un doc")
    await ad.halt_channel("test")
    assert ad.is_halted() is True
    assert await ad.claim_next_directive() is None
    # Reprise -> la directive redevient reclamable.
    await ad.resume_channel()
    assert ad.is_halted() is False
    assert (await ad.claim_next_directive())["title"] == "un doc"


@pytest.mark.asyncio
async def test_halt_blocks_new_proposals_too():
    await ad.halt_channel("gel")
    res = await ad.propose_directive("docs", "un doc")
    assert res["ok"] is False
    assert "coupe-circuit" in res["reason"]


@pytest.mark.asyncio
async def test_refuse_directive_marks_and_logs():
    await ad.propose_directive("docs", "doc ambigu")
    claimed = await ad.claim_next_directive()
    out = await ad.refuse_directive(claimed["id"], reason="consigne ambigue")
    assert out["status"] == "refused"
    assert not await ad.list_directives(status="pending")
    assert any(e["event"] == "refused" for e in await ad.read_log())
