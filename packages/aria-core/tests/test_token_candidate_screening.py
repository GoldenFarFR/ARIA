"""Sélection des candidats pour l'extraction de holders (21/07) -- flux exact
demandé par l'opérateur : découverte DexScreener/GeckoTerminal ->
liquidité/volume -> honeypot GoPlus -> extraction éligible OU liste noire
permanente (honeypot confirmé seulement, jamais un simple manque de
traction)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aria_core import token_candidate_screening as screening

CONTRACT_A = "0x" + "a" * 40
CONTRACT_B = "0x" + "b" * 40
CONTRACT_C = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(screening, "DB_PATH", str(tmp_path / "shared_test.db"))
    yield


def _no_extracted(monkeypatch, contracts=()):
    async def _fake(chain="base"):
        return [{"contract": c} for c in contracts]

    monkeypatch.setattr("aria_core.token_holder_intel.list_extracted_contracts", _fake)


def _thresholds(monkeypatch, *, liquidity=50_000.0, volume=1_000.0):
    monkeypatch.setattr("aria_core.momentum_entry._MIN_LIQUIDITY_USD", liquidity)
    monkeypatch.setattr("aria_core.momentum_entry._MIN_VOLUME_24H_USD", volume)


def _discovery(monkeypatch, contracts):
    async def _fake(*, chains=("base",)):
        return [{"contract": c, "chain": "base"} for c in contracts]

    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", _fake)


@dataclass
class _FakePair:
    base_address: str
    liquidity_usd: float
    volume_24h_usd: float
    base_symbol: str = ""


def _pairs(monkeypatch, pairs_by_contract):
    async def _fake(addresses, *, chain="base"):
        return [pairs_by_contract[a] for a in addresses if a in pairs_by_contract]

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_tokens_batch", _fake)


def _honeypot(monkeypatch, result_by_contract, *, default=(True, "", "honeypot_clear")):
    async def _fake(contract, chain):
        return result_by_contract.get(contract, default)

    monkeypatch.setattr("aria_core.momentum_entry.check_honeypot", _fake)


# ── liste noire permanente ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_blacklist_is_permanent_no_symmetric_unreject():
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is False
    await screening._blacklist_candidate(CONTRACT_A, "base", "honeypot confirmé")
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is True
    # aucune fonction de retrait n'existe -- vérifié par absence d'attribut public
    assert not hasattr(screening, "unblacklist_candidate")
    assert not hasattr(screening, "remove_from_blacklist")


@pytest.mark.asyncio
async def test_blacklist_insert_or_ignore_never_crashes_on_duplicate():
    await screening._blacklist_candidate(CONTRACT_A, "base", "raison 1")
    await screening._blacklist_candidate(CONTRACT_A, "base", "raison 2")  # ne doit pas planter
    entries = await screening.list_blacklisted_candidates()
    assert len(entries) == 1
    assert entries[0]["reason"] == "raison 1"  # la première raison est conservée


@pytest.mark.asyncio
async def test_blacklist_scoped_by_chain_not_just_contract():
    await screening._blacklist_candidate(CONTRACT_A, "base", "honeypot")
    assert await screening.is_candidate_blacklisted(CONTRACT_A, chain="solana") is False


@pytest.mark.asyncio
async def test_blacklist_normalizes_case():
    await screening._blacklist_candidate(CONTRACT_A.upper(), "base", "honeypot")
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is True


@pytest.mark.asyncio
async def test_list_blacklisted_candidates_most_recent_first():
    await screening._blacklist_candidate(CONTRACT_A, "base", "premier")
    await screening._blacklist_candidate(CONTRACT_B, "base", "second")
    entries = await screening.list_blacklisted_candidates()
    assert [e["contract"] for e in entries] == [CONTRACT_B, CONTRACT_A]


# ── screen_and_select_candidates ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discovery_failure_returns_empty(monkeypatch):
    async def _boom(*, chains=("base",)):
        raise RuntimeError("panne réseau")

    monkeypatch.setattr("aria_core.momentum_entry.discover_momentum_candidates", _boom)
    assert await screening.screen_and_select_candidates(5) == []


@pytest.mark.asyncio
async def test_empty_discovery_returns_empty(monkeypatch):
    _discovery(monkeypatch, [])
    assert await screening.screen_and_select_candidates(5) == []


@pytest.mark.asyncio
async def test_already_extracted_contracts_are_skipped(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch, contracts=[CONTRACT_A])
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []


@pytest.mark.asyncio
async def test_already_blacklisted_contracts_are_skipped(monkeypatch):
    await screening._blacklist_candidate(CONTRACT_A, "base", "honeypot confirmé")
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []


@pytest.mark.asyncio
async def test_below_liquidity_floor_excluded_but_not_blacklisted(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch, liquidity=50_000.0, volume=1_000.0)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 10_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is False


@pytest.mark.asyncio
async def test_below_volume_floor_excluded_but_not_blacklisted(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch, liquidity=50_000.0, volume=1_000.0)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 100.0, "AAA")})
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is False


@pytest.mark.asyncio
async def test_no_pair_resolved_excluded_never_selected_blindly(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {})  # dexscreener n'a rien renvoyé pour ce contrat
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []


@pytest.mark.asyncio
async def test_dexscreener_batch_failure_degrades_without_crashing(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)

    async def _boom(addresses, *, chain="base"):
        raise RuntimeError("panne dexscreener")

    monkeypatch.setattr("aria_core.services.dexscreener.fetch_tokens_batch", _boom)
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(5)
    assert result == []


@pytest.mark.asyncio
async def test_honeypot_rejected_permanently_blacklists_and_excludes(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {CONTRACT_A: (False, "taxe de vente 99%", "honeypot_rejected")})

    result = await screening.screen_and_select_candidates(5)
    assert result == []
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is True
    entries = await screening.list_blacklisted_candidates()
    assert entries[0]["reason"] == "taxe de vente 99%"


@pytest.mark.asyncio
async def test_honeypot_unavailable_excludes_but_never_blacklists(monkeypatch):
    """Panne d'infra (indisponible) -- signal jamais confirmé, jamais permanent."""
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {CONTRACT_A: (False, "GoPlus indisponible", "honeypot_unavailable")})

    result = await screening.screen_and_select_candidates(5)
    assert result == []
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is False


@pytest.mark.asyncio
async def test_honeypot_check_exception_excludes_but_never_blacklists(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})

    async def _boom(contract, chain):
        raise RuntimeError("timeout GoPlus")

    monkeypatch.setattr("aria_core.momentum_entry.check_honeypot", _boom)

    result = await screening.screen_and_select_candidates(5)
    assert result == []
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is False


@pytest.mark.asyncio
async def test_successful_candidate_is_selected_with_symbol(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA")})
    _honeypot(monkeypatch, {CONTRACT_A: (True, "", "honeypot_clear")})

    result = await screening.screen_and_select_candidates(5)
    assert result == [(CONTRACT_A, "AAA")]


@pytest.mark.asyncio
async def test_respects_limit_even_with_more_eligible_candidates(monkeypatch):
    contracts = [CONTRACT_A, CONTRACT_B, CONTRACT_C]
    _discovery(monkeypatch, contracts)
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch)
    _pairs(monkeypatch, {
        c: _FakePair(c, 100_000.0, 5_000.0, f"T{i}") for i, c in enumerate(contracts)
    })
    _honeypot(monkeypatch, {})

    result = await screening.screen_and_select_candidates(2)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_mixed_batch_one_blacklisted_one_selected_one_below_floor(monkeypatch):
    _discovery(monkeypatch, [CONTRACT_A, CONTRACT_B, CONTRACT_C])
    _no_extracted(monkeypatch)
    _thresholds(monkeypatch, liquidity=50_000.0, volume=1_000.0)
    _pairs(monkeypatch, {
        CONTRACT_A: _FakePair(CONTRACT_A, 100_000.0, 5_000.0, "AAA"),  # honeypot -> blacklist
        CONTRACT_B: _FakePair(CONTRACT_B, 10_000.0, 5_000.0, "BBB"),  # liquidité insuffisante
        CONTRACT_C: _FakePair(CONTRACT_C, 100_000.0, 5_000.0, "CCC"),  # éligible
    })
    _honeypot(monkeypatch, {CONTRACT_A: (False, "honeypot", "honeypot_rejected")})

    result = await screening.screen_and_select_candidates(10)
    assert result == [(CONTRACT_C, "CCC")]
    assert await screening.is_candidate_blacklisted(CONTRACT_A) is True
    assert await screening.is_candidate_blacklisted(CONTRACT_B) is False
