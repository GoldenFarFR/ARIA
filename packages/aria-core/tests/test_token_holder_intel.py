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
async def test_store_holders_normalizes_case_no_duplicate_across_write_reads():
    """21/07 -- bug réel trouvé en conditions réelles : cbBTC stocké une fois
    en casse checksum, une fois en lowercase, produisait DEUX lignes distinctes
    pour le MÊME contrat (Base/EVM, insensible à la casse par convention --
    même correctif que momentum_entry.normalize_contract_case). Écrire puis
    relire avec une casse différente doit toujours viser la même ligne."""
    await intel.store_holders("0xCbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "base", _HOLDERS)
    await intel.store_holders("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "base", _HOLDERS[:1])

    rows = await intel.get_holders("0xCBB7C0000AB88B473B1F5AFD9EF808440EED33BF", "base")
    assert len(rows) == 1  # la 2e écriture a bien remplacé la 1ère, pas créé une 2e ligne
    contracts = await intel.list_extracted_contracts("base")
    assert len(contracts) == 1


@pytest.mark.asyncio
async def test_store_holders_preserves_case_on_solana():
    """Solana (base58) est sensible à la casse -- jamais lowercasé, contrairement
    à Base/EVM ci-dessus."""
    mixed_case = "AbCdEfGhIjKlMnOpQrStUvWxYz1234567890ABCD"
    await intel.store_holders(mixed_case, "solana", _HOLDERS)
    contracts = await intel.list_extracted_contracts("solana")
    assert contracts[0]["contract"] == mixed_case


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
    # Normalisé en lowercase (21/07, correctif casse -- même contrat EVM ne doit
    # jamais produire deux lignes distinctes selon la casse reçue).
    assert by_contract == {"0xtoken": 2, "0xother": 1}


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
    assert {r["contract"] for r in rows} == {"0xtoken_a", "0xtoken_b"}
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


# ── list_cross_token_candidates (21/07, classement "meilleurs investisseurs") ──

_RECURRING_EOA = "0xRecurringInvestor"


def _eoa_holder(addr: str, tags: list[str] | None = None) -> dict:
    return {
        "holder_address": addr, "holder_name": None, "is_contract": False,
        "is_verified": False, "is_scam": False, "reputation": None,
        "tags": tags or [], "value": "1000",
    }


@pytest.mark.asyncio
async def test_list_cross_token_candidates_finds_recurring_eoa():
    await intel.store_holders("0xTOKEN_A", "base", [_eoa_holder(_RECURRING_EOA)])
    await intel.store_holders("0xTOKEN_B", "base", [_eoa_holder(_RECURRING_EOA)])
    await intel.store_holders("0xTOKEN_C", "base", [_eoa_holder(_RECURRING_EOA)])

    candidates = await intel.list_cross_token_candidates(min_token_count=3)
    assert len(candidates) == 1
    assert candidates[0]["holder_address"] == _RECURRING_EOA
    assert candidates[0]["token_count"] == 3


@pytest.mark.asyncio
async def test_list_cross_token_candidates_below_threshold_excluded():
    await intel.store_holders("0xTOKEN_A", "base", [_eoa_holder(_RECURRING_EOA)])
    await intel.store_holders("0xTOKEN_B", "base", [_eoa_holder(_RECURRING_EOA)])
    assert await intel.list_cross_token_candidates(min_token_count=3) == []


@pytest.mark.asyncio
async def test_list_cross_token_candidates_excludes_contracts():
    await intel.store_holders("0xTOKEN_A", "base", [_HOLDERS[0]])  # 0xPool, is_contract=True
    await intel.store_holders("0xTOKEN_B", "base", [_HOLDERS[0]])
    await intel.store_holders("0xTOKEN_C", "base", [_HOLDERS[0]])
    assert await intel.list_cross_token_candidates(min_token_count=3) == []


@pytest.mark.asyncio
async def test_list_cross_token_candidates_excludes_known_infra_tags():
    """21/07 -- vérifié en conditions réelles : sans ce filtre, le classement
    est dominé par des hot wallets d'exchange, pas des investisseurs réels."""
    exchange_eoa = "0xExchangeHotWallet"
    for i, contract in enumerate(("0xTOKEN_A", "0xTOKEN_B", "0xTOKEN_C")):
        await intel.store_holders(contract, "base", [_eoa_holder(exchange_eoa, tags=["Coinbase Exchange: Hot Wallet"])])
    assert await intel.list_cross_token_candidates(min_token_count=3) == []


@pytest.mark.asyncio
async def test_list_cross_token_candidates_sorted_by_token_count_desc():
    top = "0xTopInvestor"
    mid = "0xMidInvestor"
    for contract in ("0xTOKEN_A", "0xTOKEN_B", "0xTOKEN_C", "0xTOKEN_D"):
        await intel.store_holders(contract, "base", [_eoa_holder(top)])
    for contract in ("0xTOKEN_A", "0xTOKEN_B", "0xTOKEN_C"):
        await intel.store_holders(contract, "base", [_eoa_holder(top), _eoa_holder(mid)])

    candidates = await intel.list_cross_token_candidates(min_token_count=3)
    addrs = [c["holder_address"] for c in candidates]
    assert addrs[0] == top  # le plus de tokens en premier


@pytest.mark.asyncio
async def test_list_cross_token_candidates_scoped_to_chain():
    for contract in ("0xTOKEN_A", "0xTOKEN_B", "0xTOKEN_C"):
        await intel.store_holders(contract, "base", [_eoa_holder(_RECURRING_EOA)])
    assert await intel.list_cross_token_candidates(min_token_count=3, chain="solana") == []
