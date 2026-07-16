"""Sizing du pilote agent-wallet (#203) -- fail-closed sur toute donnée
indisponible/invalide, jamais un montant inventé. Pas d'appel réel au SDK CDP
ici, seulement des fakes injectés (même patron que test_agent_wallet_pilot.py)."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_sizing as sizing


async def _balance(value: float) -> float:
    return value


async def _balance_none() -> float | None:
    return None


async def _balance_raises() -> float:
    raise RuntimeError("API CDP indisponible")


@pytest.mark.asyncio
async def test_default_pct_is_three_percent():
    assert sizing.DEFAULT_SIZING_PCT == 0.03


@pytest.mark.asyncio
async def test_sizes_at_default_pct_below_cap():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(10.0))
    assert amount == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_sizes_at_custom_pct_below_cap():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(100.0), pct=0.05)
    assert amount == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_capped_at_max_transaction_usd_when_pct_of_balance_exceeds_it():
    # Solde large, pct large -- min(balance*pct, cap) doit choisir le plafond.
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(10_000.0), pct=0.5)
    assert amount == sizing.MAX_TRANSACTION_USD


@pytest.mark.asyncio
async def test_custom_max_usd_overrides_default_cap():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(10_000.0), pct=0.5, max_usd=2.0)
    assert amount == 2.0


@pytest.mark.asyncio
async def test_none_when_balance_fn_returns_none():
    amount = await sizing.size_trade_usd(balance_fn=_balance_none)
    assert amount is None


@pytest.mark.asyncio
async def test_none_when_balance_fn_raises():
    amount = await sizing.size_trade_usd(balance_fn=_balance_raises)
    assert amount is None


@pytest.mark.asyncio
async def test_none_when_balance_is_zero():
    # Cas réel vérifié le 16/07 : le wallet du pilote est actuellement à 0.0$.
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(0.0))
    assert amount is None


@pytest.mark.asyncio
async def test_none_when_balance_is_negative():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(-5.0))
    assert amount is None


@pytest.mark.asyncio
async def test_none_when_pct_is_zero():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(100.0), pct=0.0)
    assert amount is None


@pytest.mark.asyncio
async def test_none_when_pct_is_negative():
    amount = await sizing.size_trade_usd(balance_fn=lambda: _balance(100.0), pct=-0.1)
    assert amount is None


@pytest.mark.asyncio
async def test_max_transaction_usd_matches_pilot_module():
    from aria_core import agent_wallet_pilot as pilot

    assert sizing.MAX_TRANSACTION_USD == pilot.MAX_TRANSACTION_USD
