"""Confirmation de stabilité temporelle sur la liquidité (crible VC, item #19)."""
from __future__ import annotations

import pytest

from aria_core.skills import liquidity_stability as ls

CONTRACT = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "DB_PATH", str(tmp_path / "vc_liquidity_test.db"))


@pytest.mark.asyncio
async def test_first_scan_has_no_antecedent_is_none():
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    assert result.confirmed is None
    assert result.previous_liquidity_usd is None


@pytest.mark.asyncio
async def test_second_scan_stable_liquidity_is_confirmed():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 48_000.0)
    assert result.confirmed is True
    assert result.previous_liquidity_usd == 50_000.0


@pytest.mark.asyncio
async def test_second_scan_large_drop_is_not_confirmed():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 100_000.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 40_000.0)  # -60%
    assert result.confirmed is False


@pytest.mark.asyncio
async def test_drop_exactly_at_threshold_is_not_confirmed():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 100_000.0, max_drop_pct=40.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 60_000.0, max_drop_pct=40.0)  # -40% pile
    assert result.confirmed is False  # >= seuil, jamais confirmé (pas strictement en-dessous)


@pytest.mark.asyncio
async def test_liquidity_increase_is_confirmed_not_suspicious():
    """Une hausse de liquidité n'est jamais suspecte en soi -- seule une CHUTE l'est."""
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 200_000.0)
    assert result.confirmed is True


@pytest.mark.asyncio
async def test_upsert_keeps_only_latest_snapshot():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 48_000.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 47_000.0)
    assert result.previous_liquidity_usd == 48_000.0  # pas 50_000 (l'avant-dernier)


@pytest.mark.asyncio
async def test_different_chain_has_no_antecedent():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "solana", 1_000.0)
    assert result.confirmed is None


@pytest.mark.asyncio
async def test_different_contract_has_no_antecedent():
    await ls.record_and_check_liquidity_stability(CONTRACT, "base", 50_000.0)
    result = await ls.record_and_check_liquidity_stability("0x" + "b" * 40, "base", 1_000.0)
    assert result.confirmed is None


@pytest.mark.asyncio
async def test_malformed_contract_returns_none_without_writing():
    result = await ls.record_and_check_liquidity_stability("", "base", 50_000.0)
    assert result.confirmed is None
    # aucune écriture -- un second appel avec un vrai contrat ne doit trouver aucun antécédent
    result2 = await ls.record_and_check_liquidity_stability(CONTRACT, "base", 1.0)
    assert result2.confirmed is None


@pytest.mark.asyncio
async def test_negative_liquidity_returns_none():
    result = await ls.record_and_check_liquidity_stability(CONTRACT, "base", -5.0)
    assert result.confirmed is None
