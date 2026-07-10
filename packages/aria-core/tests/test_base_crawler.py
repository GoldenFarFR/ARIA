"""Crawler Base — découverte + absorption (déterministe, réseau injecté)."""
from __future__ import annotations

import pytest

from aria_core import base_crawler as bc


def _payload(addrs):
    return {"data": [{"relationships": {"base_token": {"data": {"id": "base_" + a}}}} for a in addrs]}


def test_extract_token_contracts_valid_and_malformed():
    a1 = "0x" + "a" * 40
    a2 = "0x" + "b" * 40
    payload = _payload([a1, a2])
    payload["data"].append({"bad": "entry"})              # ignoré
    payload["data"].append({"relationships": {}})          # ignoré
    got = bc._extract_token_contracts(payload)
    assert got == [a1, a2]


def test_extract_ignores_short_address():
    assert bc._extract_token_contracts(_payload(["0x1234"])) == []


@pytest.mark.asyncio
async def test_discover_dedupes_across_paths():
    a1, a2 = "0x" + "a" * 40, "0x" + "b" * 40

    async def fetch(path):
        return _payload([a1, a2])  # mêmes tokens sur les deux endpoints

    tokens = await bc.discover_base_tokens(fetch=fetch)
    assert tokens == [a1, a2]  # dédoublonnés


@pytest.mark.asyncio
async def test_crawl_and_absorb_counts_verdicts():
    async def discover():
        return ["0xGOOD", "0xRUG", "0xKNOWN"]

    async def absorber(contract):
        return {"0xGOOD": "kept", "0xRUG": "rejected", "0xKNOWN": "skip_rejected"}[contract]

    counts = await bc.crawl_and_absorb(discover=discover, absorber=absorber)
    assert counts == {"kept": 1, "rejected": 1, "skip_rejected": 1}


@pytest.mark.asyncio
async def test_crawl_absorber_error_is_not_fatal():
    async def discover():
        return ["0xA", "0xB"]

    async def absorber(contract):
        if contract == "0xB":
            raise RuntimeError("scan down")
        return "kept"

    counts = await bc.crawl_and_absorb(discover=discover, absorber=absorber)
    assert counts.get("kept") == 1 and counts.get("error") == 1


def _pool_payload(pairs):
    return {
        "data": [
            {
                "relationships": {"base_token": {"data": {"id": "base_" + a}}},
                "attributes": {"reserve_in_usd": str(r)},
            }
            for a, r in pairs
        ]
    }


@pytest.mark.asyncio
async def test_top_pools_filters_by_liquidity_floor():
    liquid, thin = "0x" + "a" * 40, "0x" + "b" * 40

    async def fetch(path):
        return _pool_payload([(liquid, 80_000), (thin, 5_000)])

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=30_000) == [liquid]


@pytest.mark.asyncio
async def test_top_pools_missing_reserve_is_filtered():
    a = "0x" + "c" * 40

    async def fetch(path):
        return {"data": [{"relationships": {"base_token": {"data": {"id": "base_" + a}}}}]}

    assert await bc.discover_top_pools(fetch=fetch, min_liquidity_usd=1) == []


@pytest.mark.asyncio
async def test_discover_virtuals_extracts_addresses():
    a1 = "0x" + "d" * 40

    class _VT:
        token_address = a1

    class _Client:
        async def fetch_prototypes(self):
            return [_VT(), _VT()]

    assert await bc.discover_virtuals_tokens(client=_Client()) == [a1]


@pytest.mark.asyncio
async def test_discover_virtuals_degrades_gracefully():
    class _Boom:
        async def fetch_prototypes(self):
            raise RuntimeError("down")

    assert await bc.discover_virtuals_tokens(client=_Boom()) == []


@pytest.mark.asyncio
async def test_discover_virtuals_graduated_extracts_addresses():
    a1 = "0x" + "e" * 40

    class _VT:
        token_address = a1

    class _Client:
        async def fetch_graduated(self):
            return [_VT(), _VT()]

    assert await bc.discover_virtuals_graduated_tokens(client=_Client()) == [a1]


@pytest.mark.asyncio
async def test_discover_virtuals_graduated_degrades_gracefully():
    class _Boom:
        async def fetch_graduated(self):
            raise RuntimeError("down")

    assert await bc.discover_virtuals_graduated_tokens(client=_Boom()) == []
