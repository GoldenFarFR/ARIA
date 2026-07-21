"""Stockage local des holders enrichis (Blockscout x402) -- DB isolée par test,
même patron que test_x402_budget.py/test_momentum_blacklist.py."""
from __future__ import annotations

import pytest

from aria_core import token_holder_intel as intel


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(intel, "DB_PATH", str(tmp_path / "token_holder_intel_test.db"))
    yield


_HOLDERS = [
    {
        "holder_address": "0xPool",
        "holder_name": "UniswapV3Pool",
        "is_contract": True,
        "is_verified": True,
        "is_scam": False,
        "reputation": "ok",
        "tags": ["UniswapV3Pool", "DEX"],
        "value": "1200000000000000000",
    },
    {
        "holder_address": "0xEOA",
        "holder_name": None,
        "is_contract": False,
        "is_verified": False,
        "is_scam": False,
        "reputation": None,
        "tags": [],
        "value": "50000000000000000",
    },
]


@pytest.mark.asyncio
async def test_store_and_get_holders_round_trip():
    written = await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    assert written == 2

    rows = await intel.get_holders("0xTOKEN", "base")
    assert len(rows) == 2
    addrs = {r["holder_address"] for r in rows}
    assert addrs == {"0xPool", "0xEOA"}
    pool_row = next(r for r in rows if r["holder_address"] == "0xPool")
    assert pool_row["tags"] == ["UniswapV3Pool", "DEX"]
    assert pool_row["is_contract"] == 1


@pytest.mark.asyncio
async def test_get_holders_ordered_by_value_desc():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    rows = await intel.get_holders("0xTOKEN", "base")
    assert rows[0]["holder_address"] == "0xPool"  # plus gros montant en premier


@pytest.mark.asyncio
async def test_store_holders_replaces_previous_snapshot():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    new_snapshot = [
        {"holder_address": "0xNew", "value": "999", "tags": [], "holder_name": None,
         "is_contract": False, "is_verified": False, "is_scam": False, "reputation": None},
    ]
    await intel.store_holders("0xTOKEN", "base", new_snapshot)

    rows = await intel.get_holders("0xTOKEN", "base")
    assert len(rows) == 1
    assert rows[0]["holder_address"] == "0xNew"


@pytest.mark.asyncio
async def test_store_holders_empty_list_never_erases_existing_snapshot():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    written = await intel.store_holders("0xTOKEN", "base", [])
    assert written == 0

    rows = await intel.get_holders("0xTOKEN", "base")
    assert len(rows) == 2  # snapshot précédent intact


@pytest.mark.asyncio
async def test_store_holders_missing_contract_or_chain_is_noop():
    assert await intel.store_holders("", "base", _HOLDERS) == 0
    assert await intel.store_holders("0xTOKEN", "", _HOLDERS) == 0


@pytest.mark.asyncio
async def test_get_holders_unknown_token_returns_empty():
    assert await intel.get_holders("0xNEVER", "base") == []


@pytest.mark.asyncio
async def test_last_extracted_at_none_when_never_extracted():
    assert await intel.last_extracted_at("0xNEVER", "base") is None


@pytest.mark.asyncio
async def test_last_extracted_at_set_after_store():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    assert await intel.last_extracted_at("0xTOKEN", "base") is not None


@pytest.mark.asyncio
async def test_list_extracted_contracts_reports_holder_count():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    await intel.store_holders("0xOTHER", "base", _HOLDERS[:1])

    rows = await intel.list_extracted_contracts("base")
    by_contract = {r["contract"]: r["holder_count"] for r in rows}
    assert by_contract == {"0xTOKEN": 2, "0xOTHER": 1}


@pytest.mark.asyncio
async def test_list_extracted_contracts_scoped_to_chain():
    await intel.store_holders("0xTOKEN", "base", _HOLDERS)
    rows = await intel.list_extracted_contracts("solana")
    assert rows == []


# ── wallet_cross_token_holdings (21/07, signal Sybil/coordination pour smart_money.py) ──

@pytest.mark.asyncio
async def test_wallet_cross_token_holdings_finds_wallet_across_tokens():
    await intel.store_holders("0xTOKEN_A", "base", _HOLDERS)  # contient 0xPool et 0xEOA
    await intel.store_holders("0xTOKEN_B", "base", _HOLDERS[:1])  # contient 0xPool seul

    rows = await intel.wallet_cross_token_holdings("0xPool", chain="base")
    assert {r["contract"] for r in rows} == {"0xTOKEN_A", "0xTOKEN_B"}
    assert rows[0]["tags"] == ["UniswapV3Pool", "DEX"]


@pytest.mark.asyncio
async def test_wallet_cross_token_holdings_case_insensitive():
    await intel.store_holders("0xTOKEN_A", "base", _HOLDERS)
    rows = await intel.wallet_cross_token_holdings("0XPOOL", chain="base")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_wallet_cross_token_holdings_unknown_wallet_returns_empty():
    await intel.store_holders("0xTOKEN_A", "base", _HOLDERS)
    assert await intel.wallet_cross_token_holdings("0xNeverSeen", chain="base") == []


@pytest.mark.asyncio
async def test_wallet_cross_token_holdings_empty_address_no_query():
    assert await intel.wallet_cross_token_holdings("", chain="base") == []
    assert await intel.wallet_cross_token_holdings("   ", chain="base") == []


@pytest.mark.asyncio
async def test_wallet_cross_token_holdings_scoped_to_chain():
    await intel.store_holders("0xTOKEN_A", "base", _HOLDERS)
    assert await intel.wallet_cross_token_holdings("0xPool", chain="solana") == []
