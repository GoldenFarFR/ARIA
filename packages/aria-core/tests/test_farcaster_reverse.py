"""Reverse-lookup orchestration (07/24): address -> fid (Dune) -> profile
(Warpcast). No real network call -- both underlying clients are monkeypatched
at the function level (they each already have their own dedicated test
file)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aria_core.services.farcaster_reverse import FarcasterIdentity, format_identity, reverse_lookup_address

WETH_BASE = "0x4200000000000000000000000000000000000006"


@dataclass
class _FakeFidResult:
    available: bool = True
    fid: int | None = None
    error: str | None = None


@dataclass
class _FakeProfile:
    available: bool = True
    exists: bool | None = None
    fid: int | None = None
    username: str | None = None
    display_name: str | None = None
    follower_count: int | None = None
    x_username: str | None = None
    eth_wallets: list | None = None
    error: str | None = None


@pytest.mark.asyncio
async def test_reverse_lookup_happy_path(monkeypatch):
    async def _fake_get_fid(address):
        return _FakeFidResult(available=True, fid=3)

    async def _fake_get_profile(fid):
        return _FakeProfile(
            available=True, exists=True, fid=3, username="dwr", display_name="Dan Romero",
            follower_count=109798, x_username="dwr", eth_wallets=["0xabc"],
        )

    monkeypatch.setattr("aria_core.services.dune.get_farcaster_fid_by_address", _fake_get_fid)
    monkeypatch.setattr("aria_core.services.farcaster.get_profile_by_fid", _fake_get_profile)

    result = await reverse_lookup_address(WETH_BASE)

    assert result.available is True
    assert result.found is True
    assert result.username == "dwr"
    assert result.x_username == "dwr"
    assert result.eth_wallets == ["0xabc"]


@pytest.mark.asyncio
async def test_reverse_lookup_no_verified_fid_is_not_found_not_an_error(monkeypatch):
    async def _fake_get_fid(address):
        return _FakeFidResult(available=True, fid=None)

    monkeypatch.setattr("aria_core.services.dune.get_farcaster_fid_by_address", _fake_get_fid)

    result = await reverse_lookup_address(WETH_BASE)

    assert result.available is True
    assert result.found is False
    assert result.error is None


@pytest.mark.asyncio
async def test_reverse_lookup_dune_unavailable_degrades_honestly(monkeypatch):
    async def _fake_get_fid(address):
        return _FakeFidResult(available=False, error="Dune down")

    monkeypatch.setattr("aria_core.services.dune.get_farcaster_fid_by_address", _fake_get_fid)

    result = await reverse_lookup_address(WETH_BASE)

    assert result.available is False
    assert result.error == "Dune down"


@pytest.mark.asyncio
async def test_reverse_lookup_warpcast_unavailable_degrades_honestly(monkeypatch):
    async def _fake_get_fid(address):
        return _FakeFidResult(available=True, fid=3)

    async def _fake_get_profile(fid):
        return _FakeProfile(available=False)

    monkeypatch.setattr("aria_core.services.dune.get_farcaster_fid_by_address", _fake_get_fid)
    monkeypatch.setattr("aria_core.services.farcaster.get_profile_by_fid", _fake_get_profile)

    result = await reverse_lookup_address(WETH_BASE)

    assert result.available is False


@pytest.mark.asyncio
async def test_reverse_lookup_fid_exists_false_is_not_found(monkeypatch):
    """A verified fid that Warpcast no longer resolves (deleted account) is
    treated as not-found, never a fabricated identity."""
    async def _fake_get_fid(address):
        return _FakeFidResult(available=True, fid=3)

    async def _fake_get_profile(fid):
        return _FakeProfile(available=True, exists=False)

    monkeypatch.setattr("aria_core.services.dune.get_farcaster_fid_by_address", _fake_get_fid)
    monkeypatch.setattr("aria_core.services.farcaster.get_profile_by_fid", _fake_get_profile)

    result = await reverse_lookup_address(WETH_BASE)

    assert result.available is True
    assert result.found is False
    assert result.fid == 3


def test_format_identity_unavailable():
    formatted = format_identity(FarcasterIdentity(available=False, error="boom"))
    assert "indisponible" in formatted
    assert "boom" in formatted


def test_format_identity_not_found():
    formatted = format_identity(FarcasterIdentity(available=True, found=False))
    assert "aucun compte vérifié" in formatted


def test_format_identity_full_signal():
    identity = FarcasterIdentity(
        available=True, found=True, fid=3, username="dwr", display_name="Dan Romero",
        follower_count=109798, x_username="dwr",
    )
    formatted = format_identity(identity)
    assert "@dwr" in formatted
    assert "Dan Romero" in formatted
    assert "X: @dwr" in formatted
    assert "109798 abonnés" in formatted
