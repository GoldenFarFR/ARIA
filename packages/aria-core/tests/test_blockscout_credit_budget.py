"""Suivi du budget de crédits Blockscout Pro -- 90 000 crédits/jour (90% de
100 000, palier gratuit authentifié). Vérifie le plafond dur et la remise à
zéro calendaire journalière -- même patron que ``test_x402_budget.py``."""
from __future__ import annotations

import pytest

from aria_core.services import blockscout_credit_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "DB_PATH", str(tmp_path / "blockscout_credit_budget_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.daily_status()
    assert status["cap_credits"] == 90_000
    assert status["spent_credits"] == 0
    assert status["remaining_credits"] == 90_000


@pytest.mark.asyncio
async def test_can_spend_within_cap():
    assert await budget.can_spend(20) is True
    assert await budget.can_spend(90_000) is True
    assert await budget.can_spend(90_001) is False


@pytest.mark.asyncio
async def test_can_spend_rejects_non_positive_amounts():
    assert await budget.can_spend(0) is False
    assert await budget.can_spend(-1) is False


@pytest.mark.asyncio
async def test_recorded_spend_reduces_remaining_budget():
    await budget.record_spend(endpoint="/tokens/0xabc/holders", credits=20)
    status = await budget.daily_status()
    assert status["spent_credits"] == 20
    assert status["remaining_credits"] == 89_980
    assert await budget.can_spend(89_980) is True
    assert await budget.can_spend(89_981) is False


@pytest.mark.asyncio
async def test_default_cost_per_call_is_20_credits():
    await budget.record_spend(endpoint="/addresses/0xabc")
    status = await budget.daily_status()
    assert status["spent_credits"] == 20


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded_across_multiple_spends():
    for _ in range(4499):
        await budget.record_spend(credits=20)
    # 4499 * 20 = 89 980 -- il reste tout juste 20 crédits.
    assert await budget.can_spend(20) is True
    await budget.record_spend(credits=20)
    assert await budget.can_spend(1) is False
    status = await budget.daily_status()
    assert status["remaining_credits"] == 0


@pytest.mark.asyncio
async def test_day_start_is_midnight_utc():
    from datetime import datetime, timezone

    ref = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)
    start = budget.day_start(ref)
    assert start == datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)


def test_cost_for_endpoint_token_transfers_is_30_credits():
    """22/07 -- vérifié sur le relevé réel du dashboard Blockscout : token-transfers
    coûte 30 crédits/appel, pas 20 comme le reste -- doc générique incomplète sur ce
    point précis."""
    assert budget.cost_for_endpoint("/addresses/0xabc.../token-transfers") == 30
    assert budget.cost_for_endpoint("/transactions/0xdef.../token-transfers") == 30


def test_cost_for_endpoint_default_is_20_credits():
    assert budget.cost_for_endpoint("/tokens/0xabc.../holders") == 20
    assert budget.cost_for_endpoint("/tokens/0xabc...") == 20
    assert budget.cost_for_endpoint("/addresses/0xabc.../transactions") == 20
    assert budget.cost_for_endpoint("/smart-contracts/0xabc...") == 20
