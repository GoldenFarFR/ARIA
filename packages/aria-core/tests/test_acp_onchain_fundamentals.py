"""Intégration CoinGecko (fondamentaux) dans le scoring on-chain ACP — additive et opt-in.

`include_fundamentals` est désactivé par défaut (throttle CoinGecko ~2.2s) ;
vérifie que l'absence de donnée ne dégrade jamais le score, et qu'un ratio
FDV/market cap élevé est le seul signal qui dégrade réellement le score.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.coingecko import TokenFundamentals
from aria_core.skills import acp_onchain_scan as scan

ADDR = "0x" + "a" * 40


def test_apply_fundamentals_signals_none_is_noop():
    flags: list[str] = []
    delta = scan._apply_fundamentals_signals(flags, None)
    assert delta == 0
    assert flags == []


def test_apply_fundamentals_signals_unavailable_no_penalty():
    flags: list[str] = []
    fundamentals = TokenFundamentals(
        contract=ADDR, available=False, error="donnée fondamentale indisponible (rate limit CoinGecko)"
    )
    delta = scan._apply_fundamentals_signals(flags, fundamentals)
    assert delta == 0
    assert any("indisponible" in f for f in flags)


def test_apply_fundamentals_signals_high_fdv_ratio_penalizes():
    flags: list[str] = []
    fundamentals = TokenFundamentals(
        contract=ADDR,
        available=True,
        market_cap_usd=1_000_000,
        fully_diluted_valuation_usd=5_000_000,
    )
    delta = scan._apply_fundamentals_signals(flags, fundamentals)
    assert delta == -10
    assert any("dilution" in f.lower() for f in flags)


def test_apply_fundamentals_signals_low_fdv_ratio_no_penalty():
    flags: list[str] = []
    fundamentals = TokenFundamentals(
        contract=ADDR,
        available=True,
        market_cap_usd=1_000_000,
        fully_diluted_valuation_usd=1_200_000,
    )
    delta = scan._apply_fundamentals_signals(flags, fundamentals)
    assert delta == 0
    assert not any("dilution" in f.lower() for f in flags)


def test_apply_fundamentals_signals_reports_market_cap_and_categories():
    flags: list[str] = []
    fundamentals = TokenFundamentals(
        contract=ADDR,
        available=True,
        market_cap_usd=2_500_000,
        categories=["Meme", "Base Ecosystem"],
    )
    scan._apply_fundamentals_signals(flags, fundamentals)
    assert any("market cap" in f.lower() for f in flags)
    assert any("meme" in f.lower() for f in flags)


@pytest.mark.asyncio
async def test_scan_base_token_fundamentals_disabled_by_default(monkeypatch):
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        type(scan.blockscout_client), "check_contract_flags", AsyncMock(return_value=scan.ContractFlags(address=ADDR))
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_holders", AsyncMock(return_value=scan.TokenHoldersResult())
    )
    fundamentals_mock = AsyncMock()
    monkeypatch.setattr(type(scan.coingecko_client), "get_token_fundamentals", fundamentals_mock)

    await scan.scan_base_token(ADDR)

    fundamentals_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_base_token_include_fundamentals_wires_coingecko(monkeypatch):
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        type(scan.blockscout_client), "check_contract_flags", AsyncMock(return_value=scan.ContractFlags(address=ADDR))
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_holders", AsyncMock(return_value=scan.TokenHoldersResult())
    )
    monkeypatch.setattr(
        type(scan.coingecko_client),
        "get_token_fundamentals",
        AsyncMock(
            return_value=TokenFundamentals(
                contract=ADDR,
                available=True,
                market_cap_usd=1_000_000,
                fully_diluted_valuation_usd=5_000_000,
            )
        ),
    )

    ctx = await scan.scan_base_token(ADDR, include_fundamentals=True)

    assert any("dilution" in f.lower() for f in ctx.risk_flags)
