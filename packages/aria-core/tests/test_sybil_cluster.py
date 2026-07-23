"""Détection de cluster Sybil parmi les holders d'un token (item #17)."""
from __future__ import annotations

import pytest

from aria_core.skills.sybil_cluster import (
    SybilClusterFacts,
    gather_sybil_cluster_facts,
    judge_sybil_cluster,
)

LP = "0x" + "e" * 40


class _Holder:
    def __init__(self, address, percentage):
        self.address, self.percentage = address, percentage


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_sybil_cluster(SybilClusterFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_no_cluster_is_neutral():
    v = judge_sybil_cluster(SybilClusterFacts(holders_checked=10, largest_cluster_size=2, available=True))
    assert v.signal == "neutral"


def test_large_cluster_low_cumulative_pct_is_neutral():
    """5 holders regroupés mais dust (1% cumulé) -- pas significatif."""
    v = judge_sybil_cluster(
        SybilClusterFacts(holders_checked=10, largest_cluster_size=5, largest_cluster_cumulative_pct=1.0, available=True)
    )
    assert v.signal == "neutral"


def test_small_cluster_high_cumulative_pct_is_neutral():
    """3 holders seulement -- sous le seuil de taille, même si le cumul est élevé."""
    v = judge_sybil_cluster(
        SybilClusterFacts(holders_checked=10, largest_cluster_size=3, largest_cluster_cumulative_pct=50.0, available=True)
    )
    assert v.signal == "neutral"


def test_large_cluster_high_cumulative_pct_is_concern():
    v = judge_sybil_cluster(
        SybilClusterFacts(holders_checked=15, largest_cluster_size=8, largest_cluster_cumulative_pct=45.0, available=True)
    )
    assert v.signal == "concern"
    assert any("cluster Sybil suspecté" in p for p in v.points)


# ── Récolte (Blockscout factice) ────────────────────────────────────────────


class _FakeClient:
    pass


@pytest.mark.asyncio
async def test_gather_no_holders_is_available_zero_checked():
    facts = await gather_sybil_cluster_facts([], client=_FakeClient())
    assert facts.available is True
    assert facts.holders_checked == 0


@pytest.mark.asyncio
async def test_gather_groups_by_shared_funding_source():
    holders = [_Holder(f"0x{i}" * 10, 5.0) for i in range(4)]

    async def fake_funding_source(client, wallet):
        return "0xfunder_shared", False

    facts = await gather_sybil_cluster_facts(holders, client=_FakeClient(), funding_source_fn=fake_funding_source)

    assert facts.holders_checked == 4
    assert facts.largest_cluster_size == 4
    assert facts.largest_cluster_cumulative_pct == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_gather_distinct_funding_sources_no_cluster():
    holders = [_Holder(f"0x{i}" * 10, 5.0) for i in range(4)]
    counter = {"n": 0}

    async def fake_funding_source(client, wallet):
        counter["n"] += 1
        return f"0xfunder_{counter['n']}", False  # chaque holder a sa propre source

    facts = await gather_sybil_cluster_facts(holders, client=_FakeClient(), funding_source_fn=fake_funding_source)

    assert facts.largest_cluster_size == 1


@pytest.mark.asyncio
async def test_gather_excludes_lp_address():
    holders = [_Holder(LP, 90.0), _Holder("0x" + "1" * 40, 3.0)]

    async def fake_funding_source(client, wallet):
        return "0xshared", False

    facts = await gather_sybil_cluster_facts(
        holders, exclude_addresses={LP}, client=_FakeClient(), funding_source_fn=fake_funding_source,
    )

    assert facts.holders_checked == 1  # LP jamais compté


@pytest.mark.asyncio
async def test_gather_respects_max_holders_checked():
    holders = [_Holder(f"0x{i}" * 10, 1.0) for i in range(20)]
    calls = []

    async def fake_funding_source(client, wallet):
        calls.append(wallet)
        return "0xshared", False

    await gather_sybil_cluster_facts(holders, max_holders_checked=5, client=_FakeClient(), funding_source_fn=fake_funding_source)

    assert len(calls) == 5


@pytest.mark.asyncio
async def test_gather_unresolved_source_not_counted_in_cluster():
    holders = [_Holder(f"0x{i}" * 10, 5.0) for i in range(3)]

    async def fake_funding_source(client, wallet):
        return None, False  # source jamais résolue

    facts = await gather_sybil_cluster_facts(holders, client=_FakeClient(), funding_source_fn=fake_funding_source)

    assert facts.holders_checked == 3
    assert facts.largest_cluster_size == 0


@pytest.mark.asyncio
async def test_gather_isolated_holder_failure_does_not_break_others():
    holders = [_Holder("0x" + "1" * 40, 5.0), _Holder("0x" + "2" * 40, 5.0)]

    async def fake_funding_source(client, wallet):
        if wallet == "0x" + "1" * 40:
            raise RuntimeError("panne réseau")
        return "0xshared", False

    facts = await gather_sybil_cluster_facts(holders, client=_FakeClient(), funding_source_fn=fake_funding_source)

    assert facts.holders_checked == 1
    assert facts.largest_cluster_size == 1
