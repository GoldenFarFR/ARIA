"""Registre d'approbations générique (aria_core.approvals) -- create_approval/get_approval
étaient déjà exercés indirectement via wallet_guard, mais resolve_approval() et
get_pending() n'avaient aucune couverture directe."""
from __future__ import annotations

import pytest

from aria_core import approvals


@pytest.fixture(autouse=True)
def _isolated_approvals_db(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "DB_PATH", str(tmp_path / "approvals.db"))
    yield


@pytest.mark.asyncio
async def test_create_approval_returns_pending_request():
    req = await approvals.create_approval("spend:trade_tokens", "swap test", payload="{}")
    assert req.status == approvals.ApprovalStatus.PENDING
    assert req.resolved_at is None
    assert req.resolved_by is None
    assert len(req.id) == 8


@pytest.mark.asyncio
async def test_get_approval_returns_none_for_unknown_id():
    assert await approvals.get_approval("inexistant") is None


@pytest.mark.asyncio
async def test_get_approval_round_trips_all_fields():
    req = await approvals.create_approval(
        "spend:client_fund_job", "financement job #4", payload='{"job_id": "4"}',
        requested_by="aria",
    )
    fetched = await approvals.get_approval(req.id)
    assert fetched.id == req.id
    assert fetched.action == "spend:client_fund_job"
    assert fetched.payload == '{"job_id": "4"}'
    assert fetched.requested_by == "aria"


@pytest.mark.asyncio
async def test_resolve_approval_marks_approved():
    req = await approvals.create_approval("spend:trade_tokens", "swap test")
    resolved = await approvals.resolve_approval(req.id, True, "admin1")
    assert resolved.status == approvals.ApprovalStatus.APPROVED
    assert resolved.resolved_by == "admin1"
    assert resolved.resolved_at is not None


@pytest.mark.asyncio
async def test_resolve_approval_marks_rejected():
    req = await approvals.create_approval("spend:trade_tokens", "swap test")
    resolved = await approvals.resolve_approval(req.id, False, "admin1")
    assert resolved.status == approvals.ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_resolve_approval_returns_none_for_unknown_id():
    assert await approvals.resolve_approval("inexistant", True, "admin1") is None


@pytest.mark.asyncio
async def test_resolve_approval_does_not_flip_an_already_resolved_entry():
    """La clause SQL ``WHERE status = 'pending'`` doit empêcher qu'une seconde
    résolution écrase la première (ex. rejet après un premier clic déjà traité) --
    même doctrine idempotence que wallet_guard.resolve_spend."""
    req = await approvals.create_approval("spend:trade_tokens", "swap test")
    first = await approvals.resolve_approval(req.id, True, "admin1")
    second = await approvals.resolve_approval(req.id, False, "admin2")

    assert first.status == approvals.ApprovalStatus.APPROVED
    # La ligne existe toujours et reste APPROVED -- le second essai (rejet) ne
    # l'a jamais réécrite, même s'il renvoie la ligne inchangée sans erreur.
    assert second.status == approvals.ApprovalStatus.APPROVED
    assert second.resolved_by == "admin1"


@pytest.mark.asyncio
async def test_get_pending_excludes_resolved_entries():
    pending_req = await approvals.create_approval("spend:trade_tokens", "en attente")
    resolved_req = await approvals.create_approval("spend:client_fund_job", "déjà tranchée")
    await approvals.resolve_approval(resolved_req.id, True, "admin1")

    pending = await approvals.get_pending()
    pending_ids = {p.id for p in pending}
    assert pending_req.id in pending_ids
    assert resolved_req.id not in pending_ids


@pytest.mark.asyncio
async def test_get_pending_empty_when_nothing_pending():
    assert await approvals.get_pending() == []
