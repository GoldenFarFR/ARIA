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
