"""Extraction récurrente des holders (Blockscout x402), coordonnée vers le
classement smart-money (21/07). Vérifie : gating, tiers de profondeur par
capitalisation, sélection des tokens jamais encore extraits, kill-switch,
dégradation propre sur panne CoinGecko/Blockscout."""
from __future__ import annotations

import pytest

from aria_core import screened_pool, token_holder_intel
from aria_core.services import token_holder_extraction_cycle as cycle

CONTRACT_A = "0x" + "a" * 40
CONTRACT_B = "0x" + "b" * 40
CONTRACT_C = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "shared_test.db")
    monkeypatch.setattr(cycle, "DB_PATH", db_path)
    monkeypatch.setattr(screened_pool, "DB_PATH", db_path)
    monkeypatch.setattr(token_holder_intel, "DB_PATH", db_path)
    yield


def test_disabled_by_default():
    assert cycle.token_holder_extraction_enabled() is False


def test_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    assert cycle.token_holder_extraction_enabled() is True


# ── target_holder_count (paliers par capitalisation, décision opérateur 21/07) ──

@pytest.mark.parametrize("market_cap,expected", [
    (2_000_000_000.0, 500),
    (1_000_000_000.0, 500),  # pile au seuil
    (999_999_999.0, 300),
    (500_000_000.0, 300),
    (499_999_999.0, 200),
    (100_000_000.0, 200),
    (99_999_999.0, 100),
    (1_000.0, 100),
    (None, 100),  # capitalisation inconnue -> jamais un tier supérieur inventé
])
def test_target_holder_count_tiers(market_cap, expected):
    assert cycle.target_holder_count(market_cap) == expected


# ── _select_next_tokens ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_select_next_tokens_excludes_already_extracted():
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=100_000.0)
    await screened_pool.upsert_screened(contract=CONTRACT_B, symbol="BBB", liquidity_usd=50_000.0)
    await token_holder_intel.store_holders(
        CONTRACT_A, "base",
        [{"holder_address": "0xHolder", "holder_name": None, "is_contract": False,
          "is_verified": False, "is_scam": False, "reputation": None, "tags": [], "value": "1"}],
    )

    selected = await cycle._select_next_tokens(10)
    assert selected == [(CONTRACT_B, "BBB")]


@pytest.mark.asyncio
async def test_select_next_tokens_ordered_by_liquidity_desc():
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=10_000.0)
    await screened_pool.upsert_screened(contract=CONTRACT_B, symbol="BBB", liquidity_usd=500_000.0)
    await screened_pool.upsert_screened(contract=CONTRACT_C, symbol="CCC", liquidity_usd=100_000.0)

    selected = await cycle._select_next_tokens(10)
    assert [c for c, _ in selected] == [CONTRACT_B, CONTRACT_C, CONTRACT_A]


@pytest.mark.asyncio
async def test_select_next_tokens_respects_limit():
    for i in range(5):
        await screened_pool.upsert_screened(contract=f"0x{i:040d}", symbol=f"T{i}", liquidity_usd=float(i))
    selected = await cycle._select_next_tokens(2)
    assert len(selected) == 2


@pytest.mark.asyncio
async def test_select_next_tokens_scoped_to_base_network():
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=1.0, network="solana")
    assert await cycle._select_next_tokens(10) == []


# ── run_token_holder_extraction_cycle ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_skipped_when_gate_off():
    result = await cycle.run_token_holder_extraction_cycle()
    assert result == {"outcome": "skipped", "reason": "gate_off"}


@pytest.mark.asyncio
async def test_cycle_respects_kill_switch(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await cycle.run_token_holder_extraction_cycle()
    assert result == {"outcome": "skipped", "reason": "paused"}


@pytest.mark.asyncio
async def test_cycle_no_candidate(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    result = await cycle.run_token_holder_extraction_cycle()
    assert result == {"outcome": "no_candidate"}


class _FakeFundamentals:
    def __init__(self, *, available, market_cap_usd=None):
        self.available = available
        self.market_cap_usd = market_cap_usd


@pytest.mark.asyncio
async def test_cycle_extracts_and_stores_with_correct_tier(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=1_000_000.0)

    async def _fake_fundamentals(contract, *, platform_id="base"):
        return _FakeFundamentals(available=True, market_cap_usd=2_000_000_000.0)  # tier 500

    captured_target = {}

    async def _fake_paginated(contract, *, chain, target_count, token_symbol):
        captured_target["value"] = target_count
        return [
            {"holder_address": f"0xH{i}", "holder_name": None, "is_contract": False,
             "is_verified": False, "is_scam": False, "reputation": None, "tags": [], "value": "1"}
            for i in range(3)
        ]

    monkeypatch.setattr(
        "aria_core.services.coingecko.coingecko_client.get_token_fundamentals", _fake_fundamentals,
    )
    monkeypatch.setattr(
        "aria_core.services.blockscout_x402.get_token_holders_x402_paginated", _fake_paginated,
    )

    result = await cycle.run_token_holder_extraction_cycle()
    assert result["outcome"] == "ok"
    assert len(result["tokens_processed"]) == 1
    proc = result["tokens_processed"][0]
    assert proc["contract"] == CONTRACT_A
    assert proc["target_count"] == 500
    assert proc["holders_stored"] == 3
    assert captured_target["value"] == 500

    stored = await token_holder_intel.get_holders(CONTRACT_A, "base")
    assert len(stored) == 3


@pytest.mark.asyncio
async def test_cycle_unknown_market_cap_falls_back_to_default_tier(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=1_000.0)

    async def _fake_fundamentals(contract, *, platform_id="base"):
        return _FakeFundamentals(available=False)  # panne CoinGecko / non listé

    captured_target = {}

    async def _fake_paginated(contract, *, chain, target_count, token_symbol):
        captured_target["value"] = target_count
        return []

    monkeypatch.setattr(
        "aria_core.services.coingecko.coingecko_client.get_token_fundamentals", _fake_fundamentals,
    )
    monkeypatch.setattr(
        "aria_core.services.blockscout_x402.get_token_holders_x402_paginated", _fake_paginated,
    )

    result = await cycle.run_token_holder_extraction_cycle()
    assert captured_target["value"] == 100


@pytest.mark.asyncio
async def test_cycle_empty_extraction_stores_nothing_but_never_crashes(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=1_000.0)

    async def _fake_fundamentals(contract, *, platform_id="base"):
        return _FakeFundamentals(available=False)

    async def _fake_paginated(contract, *, chain, target_count, token_symbol):
        return []  # budget épuisé / panne réseau

    monkeypatch.setattr(
        "aria_core.services.coingecko.coingecko_client.get_token_fundamentals", _fake_fundamentals,
    )
    monkeypatch.setattr(
        "aria_core.services.blockscout_x402.get_token_holders_x402_paginated", _fake_paginated,
    )

    result = await cycle.run_token_holder_extraction_cycle()
    assert result["outcome"] == "ok"
    assert result["tokens_processed"][0]["holders_stored"] == 0


@pytest.mark.asyncio
async def test_cycle_exception_on_one_token_never_blocks_the_others(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=2_000.0)
    await screened_pool.upsert_screened(contract=CONTRACT_B, symbol="BBB", liquidity_usd=1_000.0)

    async def _fake_fundamentals(contract, *, platform_id="base"):
        return _FakeFundamentals(available=False)

    async def _fake_paginated(contract, *, chain, target_count, token_symbol):
        if contract == CONTRACT_A:
            raise RuntimeError("panne réseau")
        return [
            {"holder_address": "0xH", "holder_name": None, "is_contract": False,
             "is_verified": False, "is_scam": False, "reputation": None, "tags": [], "value": "1"},
        ]

    monkeypatch.setattr(
        "aria_core.services.coingecko.coingecko_client.get_token_fundamentals", _fake_fundamentals,
    )
    monkeypatch.setattr(
        "aria_core.services.blockscout_x402.get_token_holders_x402_paginated", _fake_paginated,
    )

    result = await cycle.run_token_holder_extraction_cycle()
    by_contract = {p["contract"]: p["holders_stored"] for p in result["tokens_processed"]}
    assert by_contract[CONTRACT_A] == 0
    assert by_contract[CONTRACT_B] == 1


@pytest.mark.asyncio
async def test_cycle_notifies_summary_when_holders_stored(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    await screened_pool.upsert_screened(contract=CONTRACT_A, symbol="AAA", liquidity_usd=1_000.0)

    async def _fake_fundamentals(contract, *, platform_id="base"):
        return _FakeFundamentals(available=False)

    async def _fake_paginated(contract, *, chain, target_count, token_symbol):
        return [
            {"holder_address": "0xH", "holder_name": None, "is_contract": False,
             "is_verified": False, "is_scam": False, "reputation": None, "tags": [], "value": "1"},
        ]

    monkeypatch.setattr(
        "aria_core.services.coingecko.coingecko_client.get_token_fundamentals", _fake_fundamentals,
    )
    monkeypatch.setattr(
        "aria_core.services.blockscout_x402.get_token_holders_x402_paginated", _fake_paginated,
    )

    notified = []

    async def _notifier(text):
        notified.append(text)

    await cycle.run_token_holder_extraction_cycle(notifier=_notifier)
    assert len(notified) == 1
    assert "AAA" in notified[0]
