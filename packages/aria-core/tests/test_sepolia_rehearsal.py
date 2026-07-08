"""Orchestration du rehearsal Sepolia — prépare + escalade, ne signe jamais ici."""
from __future__ import annotations

import pytest

from aria_core.onchain import sepolia_rehearsal as rehearsal

RECORDS = [{"contract": "0xabc", "verdict": "BUY"}]


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LEDGER_ADDRESS", "0x000000000000000000000000000000000000dEaD")
    yield


@pytest.mark.asyncio
async def test_none_when_anchor_seam_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_ONCHAIN_ANCHOR_ENABLED", raising=False)
    assert await rehearsal.escalate_sepolia_anchor(RECORDS) is None


@pytest.mark.asyncio
async def test_none_when_no_ledger_configured(monkeypatch):
    monkeypatch.delenv("ARIA_LEDGER_ADDRESS", raising=False)
    assert await rehearsal.escalate_sepolia_anchor(RECORDS) is None


@pytest.mark.asyncio
async def test_none_when_no_records():
    assert await rehearsal.escalate_sepolia_anchor([]) is None


@pytest.mark.asyncio
async def test_escalates_with_sepolia_chain_id_locked(monkeypatch):
    captured = {}

    async def fake_escalate_spend(action, *, amount, counterparty, description, payload):
        captured["action"] = action
        captured["payload"] = payload
        return "approval-123"

    monkeypatch.setattr("aria_core.wallet_guard.escalate_spend", fake_escalate_spend)

    result = await rehearsal.escalate_sepolia_anchor(RECORDS)
    assert result == "approval-123"
    assert captured["action"] == "onchain_anchor_sepolia"
    # Verrouillé Sepolia (84532) quel que soit ARIA_ONCHAIN_CHAIN_ID — jamais mainnet ici.
    assert captured["payload"]["chain_id"] == 84532
    assert captured["payload"]["contract"] == "0x000000000000000000000000000000000000dEaD"
    assert captured["payload"]["root"].startswith("0x")
