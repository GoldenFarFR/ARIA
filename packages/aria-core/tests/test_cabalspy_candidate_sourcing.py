"""Sourcing CabalSpy -- KOL directory, all chains categorized (23/07).

25/08 -- the "real sourcing into scoring" track (Base wallets enqueued into
wallet_scan_queue) was removed along with the entire wallet-scoring
mechanism (operator decision). This module is now a pure read-only KOL
directory, no downstream pipeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.services.cabalspy import CabalSpyWallet
from aria_core.skills import cabalspy_candidate_sourcing as sourcing

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sourcing, "DB_PATH", str(tmp_path / "cabalspy_test.db"))
    yield


def _wallet(address: str, blockchain: str, *, wallet_type: str = "kol", name: str = "someone") -> CabalSpyWallet:
    return CabalSpyWallet(wallet_address=address, blockchain=blockchain, type=wallet_type, name=name)


@pytest.mark.asyncio
async def test_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_CABALSPY_SOURCING_ENABLED", raising=False)
    result = await sourcing.run_cabalspy_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "gate_off"}


@pytest.mark.asyncio
async def test_skips_when_no_api_key(monkeypatch):
    monkeypatch.setenv("ARIA_CABALSPY_SOURCING_ENABLED", "true")
    monkeypatch.delenv("CABALSPY_API_KEY", raising=False)
    result = await sourcing.run_cabalspy_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "no_api_key"}


@pytest.mark.asyncio
async def test_categorizes_all_chains(monkeypatch):
    monkeypatch.setenv("ARIA_CABALSPY_SOURCING_ENABLED", "true")
    monkeypatch.setenv("CABALSPY_API_KEY", "test-key")

    async def _fake_list_wallets(blockchain, *, wallet_type="kol", page_limit=100):
        return {
            "base": [_wallet("0xBASE1", "base"), _wallet("0xBASE2", "base")],
            "bnb": [_wallet("0xBNB1", "bnb")],
            "solana": [_wallet("SoLaNaAddr1", "solana")],
        }.get(blockchain, [])

    monkeypatch.setattr("aria_core.services.cabalspy.list_wallets", _fake_list_wallets)

    result = await sourcing.run_cabalspy_candidate_sourcing_cycle(now=NOW)

    assert result["outcome"] == "ok"
    assert result["stored_per_chain"] == {"base": 2, "bnb": 1, "solana": 1}

    all_catalogued = await sourcing.catalogued_wallets()
    assert {w["wallet"] for w in all_catalogued} == {"0xbase1", "0xbase2", "0xbnb1", "solanaaddr1"}
    bnb_only = await sourcing.catalogued_wallets("bnb")
    assert len(bnb_only) == 1
    assert bnb_only[0]["blockchain"] == "bnb"


@pytest.mark.asyncio
async def test_resync_not_due_skips_without_refetching(monkeypatch):
    monkeypatch.setenv("ARIA_CABALSPY_SOURCING_ENABLED", "true")
    monkeypatch.setenv("CABALSPY_API_KEY", "test-key")

    calls = {"n": 0}

    async def _fake_list_wallets(blockchain, *, wallet_type="kol", page_limit=100):
        calls["n"] += 1
        return [_wallet("0xBASE1", "base")]

    monkeypatch.setattr("aria_core.services.cabalspy.list_wallets", _fake_list_wallets)

    first = await sourcing.run_cabalspy_candidate_sourcing_cycle(now=NOW)
    assert first["outcome"] == "ok"
    assert calls["n"] == 3  # base + bnb + solana

    second = await sourcing.run_cabalspy_candidate_sourcing_cycle(now=NOW + timedelta(days=1))
    assert second["outcome"] == "skipped"
    assert second["reason"] == "resync_not_due"
    assert calls["n"] == 3  # aucun appel supplémentaire -- économie de crédits confirmée

    third = await sourcing.run_cabalspy_candidate_sourcing_cycle(now=NOW + timedelta(days=8))
    assert third["outcome"] == "ok"
    assert calls["n"] == 6  # re-synchronisation après le délai minimum


@pytest.mark.asyncio
async def test_paused_skips(monkeypatch):
    monkeypatch.setenv("ARIA_CABALSPY_SOURCING_ENABLED", "true")
    monkeypatch.setenv("CABALSPY_API_KEY", "test-key")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda: True)

    result = await sourcing.run_cabalspy_candidate_sourcing_cycle()
    assert result == {"outcome": "skipped", "reason": "paused"}


@pytest.mark.asyncio
async def test_notifier_called_with_summary(monkeypatch):
    monkeypatch.setenv("ARIA_CABALSPY_SOURCING_ENABLED", "true")
    monkeypatch.setenv("CABALSPY_API_KEY", "test-key")

    async def _fake_list_wallets(blockchain, *, wallet_type="kol", page_limit=100):
        return [_wallet("0xBASE1", "base")] if blockchain == "base" else []

    monkeypatch.setattr("aria_core.services.cabalspy.list_wallets", _fake_list_wallets)

    notified = []

    async def _notifier(msg):
        notified.append(msg)

    await sourcing.run_cabalspy_candidate_sourcing_cycle(notifier=_notifier, now=NOW)
    assert len(notified) == 1
    assert "CabalSpy" in notified[0]
