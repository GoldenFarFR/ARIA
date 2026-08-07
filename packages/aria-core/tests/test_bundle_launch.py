"""Signal 'bundle & sniper au lancement' -- achat groupé coordonné réparti sur
plusieurs wallets dans un même bloc (backlog #258, 07/08)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aria_core.services.dune import build_bundle_launch_query
from aria_core.skills.bundle_launch import (
    BundleLaunchFacts,
    gather_bundle_launch_facts,
    judge_bundle_launch,
)

TOKEN = "0x" + "a" * 40


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_bundle_launch(BundleLaunchFacts(available=False, error="clé absente"))
    assert v.signal == "unknown"


def test_no_block_found_is_unknown():
    """Zéro bloc analysé n'est pas 'sain' : c'est une absence de donnée."""
    v = judge_bundle_launch(BundleLaunchFacts(blocks_examined=0, available=True))
    assert v.signal == "unknown"


def test_many_buyers_in_one_block_is_concern():
    v = judge_bundle_launch(BundleLaunchFacts(
        blocks_examined=3, max_buyers_in_one_block=9,
        bundled_block_number=123456, bundled_block_usd=42_000.0, available=True,
    ))
    assert v.signal == "concern"
    assert any("9 acheteurs distincts" in p for p in v.points)
    assert any("123456" in p for p in v.points)


def test_concern_states_the_funding_limit_rather_than_claiming_proof():
    """Honnêteté : co-achat même bloc = coordination, jamais preuve d'un
    propriétaire unique (des snipers indépendants produisent la même trace)."""
    v = judge_bundle_launch(BundleLaunchFacts(
        blocks_examined=3, max_buyers_in_one_block=8, available=True,
    ))
    assert any("non vérifié ici" in p for p in v.points)


def test_organic_launch_is_neutral():
    v = judge_bundle_launch(BundleLaunchFacts(
        blocks_examined=3, max_buyers_in_one_block=2, available=True,
    ))
    assert v.signal == "neutral"


# ── Collecte (dune injecté, zéro réseau) ────────────────────────────────────


@dataclass
class _FakeBlock:
    block_number: int
    distinct_buyers: int
    bought_usd: float = 0.0


@dataclass
class _FakeResult:
    blocks: list = field(default_factory=list)
    available: bool = True
    error: str | None = None


class _FakeDune:
    def __init__(self, result):
        self._result = result

    async def get_token_bundle_launch(self, contract, *, blockchain="base", lookback_days=90):
        return self._result


@pytest.mark.asyncio
async def test_gather_keeps_only_the_launch_window_not_later_trading():
    """Un pic d'acheteurs APRÈS la fenêtre de lancement ne doit pas être lu
    comme un bundle -- c'est du trading normal sur un token qui décolle."""
    result = _FakeResult(blocks=[
        _FakeBlock(100, 2), _FakeBlock(101, 1), _FakeBlock(102, 2),
        _FakeBlock(103, 50),  # hors fenêtre : ignoré
    ])
    facts = await gather_bundle_launch_facts(TOKEN, dune_module=_FakeDune(result))
    assert facts.blocks_examined == 3
    assert facts.max_buyers_in_one_block == 2
    assert judge_bundle_launch(facts).signal == "neutral"


@pytest.mark.asyncio
async def test_gather_reports_the_worst_block_of_the_window():
    result = _FakeResult(blocks=[
        _FakeBlock(100, 2), _FakeBlock(101, 7, 15_000.0), _FakeBlock(102, 1),
    ])
    facts = await gather_bundle_launch_facts(TOKEN, dune_module=_FakeDune(result))
    assert facts.max_buyers_in_one_block == 7
    assert facts.bundled_block_number == 101
    assert judge_bundle_launch(facts).signal == "concern"


@pytest.mark.asyncio
async def test_gather_propagates_unavailability_never_invents():
    result = _FakeResult(available=False, error="dune indisponible")
    facts = await gather_bundle_launch_facts(TOKEN, dune_module=_FakeDune(result))
    assert facts.available is False
    assert judge_bundle_launch(facts).signal == "unknown"


# ── Requête SQL : anti-injection (même doctrine que les autres) ─────────────


def test_query_rejects_a_non_evm_contract():
    with pytest.raises(ValueError):
        build_bundle_launch_query("'; DROP TABLE dex.trades; --")


def test_query_rejects_an_injected_blockchain():
    with pytest.raises(ValueError):
        build_bundle_launch_query(TOKEN, blockchain="base' OR '1'='1")


def test_query_emits_a_bare_hex_literal_never_quoted():
    sql = build_bundle_launch_query(TOKEN)
    assert f"token_bought_address = {TOKEN}" in sql
    assert f"'{TOKEN}'" not in sql
