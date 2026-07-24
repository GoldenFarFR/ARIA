"""Registre de découverte multi-launchpad — catégorisation bonding/direct/unknown,
agrégation best-effort (un launchpad en panne n'arrête pas les autres)."""
from __future__ import annotations

import pytest

from aria_core.services import launchpad_discovery as ld


def test_registry_has_expected_categories():
    by_key = {a.key: a for a in ld.list_adapters()}
    assert by_key["virtuals_bonding"].category == "bonding"
    assert by_key["virtuals_graduated"].category == "direct"
    assert by_key["clanker"].category == "direct"
    assert by_key["flaunch"].category == "direct"
    assert by_key["bankr"].category == "unknown"
    assert by_key["ape_store"].category == "unknown"
    assert by_key["mint_club"].category == "unknown"


def test_seams_have_no_discoverer():
    # 24/07 -- flaunch removed from this list: it now has a real on-chain
    # discoverer (services/flaunch.py), see test_flaunch_has_discoverer below.
    for key in ("zora", "bankr", "ape_store", "mint_club"):
        adapter = next(a for a in ld.list_adapters() if a.key == key)
        assert adapter.discover is None


def test_flaunch_has_discoverer():
    adapter = next(a for a in ld.list_adapters() if a.key == "flaunch")
    assert adapter.discover is not None


def test_list_adapters_filters_by_category():
    bonding = ld.list_adapters(category="bonding")
    assert {a.key for a in bonding} == {"virtuals_bonding"}
    direct = ld.list_adapters(category="direct")
    assert {a.key for a in direct} == {"virtuals_graduated", "clanker", "flaunch", "zora"}


@pytest.mark.asyncio
async def test_discover_bonding_candidates_calls_only_bonding_adapters(monkeypatch):
    async def fake_virtuals_bonding(*, limit):
        return ["0xBOND1"]

    monkeypatch.setattr(ld, "_discover_virtuals_bonding", fake_virtuals_bonding)
    monkeypatch.setitem(
        ld._ADAPTERS,
        "virtuals_bonding",
        ld.LaunchpadAdapter("virtuals_bonding", "Virtuals Protocol (bonding)", "bonding", fake_virtuals_bonding),
    )

    result = await ld.discover_bonding_candidates()
    assert result == {"virtuals_bonding": ["0xBOND1"]}


@pytest.mark.asyncio
async def test_discover_direct_candidates_aggregates_multiple_launchpads(monkeypatch):
    async def fake_graduated(*, limit):
        return ["0xGRAD1"]

    async def fake_clanker(*, limit):
        return ["0xCLANK1", "0xCLANK2"]

    monkeypatch.setitem(
        ld._ADAPTERS,
        "virtuals_graduated",
        ld.LaunchpadAdapter("virtuals_graduated", "Virtuals Protocol (gradué)", "direct", fake_graduated),
    )
    monkeypatch.setitem(
        ld._ADAPTERS,
        "clanker",
        ld.LaunchpadAdapter("clanker", "Clanker", "direct", fake_clanker),
    )
    monkeypatch.setitem(
        ld._ADAPTERS, "flaunch", ld.LaunchpadAdapter("flaunch", "Flaunch", "direct", None)
    )
    monkeypatch.setitem(ld._ADAPTERS, "zora", ld.LaunchpadAdapter("zora", "Zora", "direct", None))

    result = await ld.discover_direct_candidates()
    assert result == {"virtuals_graduated": ["0xGRAD1"], "clanker": ["0xCLANK1", "0xCLANK2"]}
    assert "flaunch" not in result  # seam vide : jamais appelé, jamais dans le résultat


@pytest.mark.asyncio
async def test_one_launchpad_failure_does_not_block_others(monkeypatch):
    async def fake_ok(*, limit):
        return ["0xOK"]

    async def fake_boom(*, limit):
        raise RuntimeError("service down")

    monkeypatch.setitem(
        ld._ADAPTERS,
        "virtuals_graduated",
        ld.LaunchpadAdapter("virtuals_graduated", "Virtuals Protocol (gradué)", "direct", fake_boom),
    )
    monkeypatch.setitem(
        ld._ADAPTERS,
        "clanker",
        ld.LaunchpadAdapter("clanker", "Clanker", "direct", fake_ok),
    )
    monkeypatch.setitem(
        ld._ADAPTERS, "flaunch", ld.LaunchpadAdapter("flaunch", "Flaunch", "direct", None)
    )
    monkeypatch.setitem(ld._ADAPTERS, "zora", ld.LaunchpadAdapter("zora", "Zora", "direct", None))

    result = await ld.discover_direct_candidates()
    assert result["virtuals_graduated"] == []  # échec -> liste vide, pas d'exception
    assert result["clanker"] == ["0xOK"]


@pytest.mark.asyncio
async def test_discover_clanker_direct_extracts_addresses(monkeypatch):
    class _Token:
        def __init__(self, addr):
            self.contract_address = addr

    class _FakeClankerClient:
        async def fetch_recent(self, limit):
            return [_Token("0x" + "a" * 40), _Token("0x" + "a" * 40), _Token("0x" + "b" * 40)]

    monkeypatch.setattr(
        "aria_core.services.clanker.clanker_client", _FakeClankerClient()
    )
    addrs = await ld._discover_clanker_direct(limit=50)
    assert addrs == ["0x" + "a" * 40, "0x" + "b" * 40]


@pytest.mark.asyncio
async def test_discover_clanker_direct_degrades_gracefully(monkeypatch):
    class _Boom:
        async def fetch_recent(self, limit):
            raise RuntimeError("down")

    monkeypatch.setattr("aria_core.services.clanker.clanker_client", _Boom())
    assert await ld._discover_clanker_direct(limit=50) == []


@pytest.mark.asyncio
async def test_discover_flaunch_direct_extracts_addresses(monkeypatch):
    class _Token:
        def __init__(self, addr):
            self.contract = addr

    class _FakeFlaunchClient:
        async def fetch_recent(self, limit):
            return [_Token("0x" + "a" * 40), _Token("0x" + "a" * 40), _Token("0x" + "b" * 40)]

    monkeypatch.setattr(
        "aria_core.services.flaunch.flaunch_client", _FakeFlaunchClient()
    )
    addrs = await ld._discover_flaunch_direct(limit=50)
    assert addrs == ["0x" + "a" * 40, "0x" + "b" * 40]


@pytest.mark.asyncio
async def test_discover_flaunch_direct_degrades_gracefully(monkeypatch):
    class _Boom:
        async def fetch_recent(self, limit):
            raise RuntimeError("down")

    monkeypatch.setattr("aria_core.services.flaunch.flaunch_client", _Boom())
    assert await ld._discover_flaunch_direct(limit=50) == []
