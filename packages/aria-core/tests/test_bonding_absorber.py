"""Absorbeur niche bonding (DB isolée) — jamais network='base' (pool 85% VC)."""
from __future__ import annotations

import pytest

from aria_core import screened_pool as sp
from aria_core.skills import bonding_absorber as ba
from aria_core.skills.acp_onchain_scan import TokenScanContext


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "bonding_absorb_test.db"))
    yield


def _bonding_ctx(contract: str, **overrides) -> TokenScanContext:
    defaults = dict(
        contract=contract, valid_address=True,
        bonding_phase=True, bonding_progress=0.6,
        security_score=78, lite_verdict="SAFE",
        contract_verified=True, has_mint=True,
        mint_authority="launchpad", mint_authority_detail="déployé par Virtuals",
        has_blacklist=False, has_disable_transfers=False, dev_signal="aligned",
    )
    defaults.update(overrides)
    return TokenScanContext(**defaults)


def _scanner(ctx_by_contract):
    async def _scan(contract, **kw):
        return ctx_by_contract[contract]
    return _scan


@pytest.mark.asyncio
async def test_healthy_bonding_candidate_kept_in_bonding_pool():
    scan = _scanner({"0xbond": _bonding_ctx("0xbond")})
    assert await ba.absorb_bonding_candidate("0xbond", scanner=scan) == "kept"
    assert await sp.get_status("0xbond") == "active"
    # Invisible du pool VC standard (network="base" par défaut).
    assert await sp.count_pool("active") == 0
    assert await sp.count_pool("active", network=ba.BONDING_NETWORK) == 1
    row = (await sp.list_pool(network=ba.BONDING_NETWORK))[0]
    assert row["network"] == ba.BONDING_NETWORK
    assert "bonding" in row["screen_reason"]


@pytest.mark.asyncio
async def test_confirmed_bad_actor_rejected_forever():
    scan = _scanner({"0xrug": _bonding_ctx("0xrug", has_blacklist=True)})
    assert await ba.absorb_bonding_candidate("0xrug", scanner=scan) == "rejected"
    assert await sp.get_status("0xrug") == "rejected"
    assert await ba.absorb_bonding_candidate("0xrug", scanner=scan) == "skip_rejected"


@pytest.mark.asyncio
async def test_soft_failure_recorded_pending_not_banned():
    scan = _scanner({"0xsoft": _bonding_ctx("0xsoft", mint_authority="unknown")})
    assert await ba.absorb_bonding_candidate("0xsoft", scanner=scan) == "skip_incomplete"
    assert await sp.get_status("0xsoft") == "pending"
    # Pas un rejet définitif : un re-scan (force=True) peut encore le récupérer.
    scan2 = _scanner({"0xsoft": _bonding_ctx("0xsoft", mint_authority="launchpad")})
    assert await ba.absorb_bonding_candidate("0xsoft", scanner=scan2, force=True) == "kept"


@pytest.mark.asyncio
async def test_already_active_short_circuits():
    scan = _scanner({"0xbond": _bonding_ctx("0xbond")})
    assert await ba.absorb_bonding_candidate("0xbond", scanner=scan) == "kept"
    assert await ba.absorb_bonding_candidate("0xbond", scanner=scan) == "skip_active"


@pytest.mark.asyncio
async def test_discover_and_absorb_aggregates_across_launchpads():
    async def fake_discover(*, limit_per_launchpad):
        return {"virtuals_bonding": ["0xa", "0xb"], "other_launchpad": ["0xc"]}

    scan = _scanner({
        "0xa": _bonding_ctx("0xa"),
        "0xb": _bonding_ctx("0xb", has_blacklist=True),
        "0xc": _bonding_ctx("0xc", mint_authority="unknown"),
    })

    async def fake_absorb(contract):
        return await ba.absorb_bonding_candidate(contract, scanner=scan)

    counts = await ba.discover_and_absorb_bonding(discover=fake_discover, absorber=fake_absorb)
    assert counts == {"kept": 1, "rejected": 1, "skip_incomplete": 1}


@pytest.mark.asyncio
async def test_one_candidate_error_does_not_block_others():
    async def fake_discover(*, limit_per_launchpad):
        return {"virtuals_bonding": ["0xok", "0xboom"]}

    async def fake_absorb(contract):
        if contract == "0xboom":
            raise RuntimeError("scan down")
        return "kept"

    counts = await ba.discover_and_absorb_bonding(discover=fake_discover, absorber=fake_absorb)
    assert counts == {"kept": 1, "error": 1}


def test_bonding_discovery_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_BONDING_DISCOVERY_ENABLED", raising=False)
    assert ba.bonding_discovery_enabled() is False


def test_bonding_discovery_enabled_via_env(monkeypatch):
    monkeypatch.setenv("ARIA_BONDING_DISCOVERY_ENABLED", "1")
    assert ba.bonding_discovery_enabled() is True


@pytest.mark.asyncio
async def test_run_bonding_discovery_cycle_combines_both_volets(monkeypatch):
    async def fake_discover_and_absorb_bonding(*, limit_per_launchpad=50):
        return {"kept": 1}

    async def fake_discover_direct(*, limit_per_launchpad):
        return {"clanker": ["0xdirect1", "0xdirect2"]}

    async def fake_absorb_standard(contract):
        return "kept" if contract == "0xdirect1" else "rejected"

    monkeypatch.setattr(ba, "discover_and_absorb_bonding", fake_discover_and_absorb_bonding)
    monkeypatch.setattr(
        "aria_core.services.launchpad_discovery.discover_direct_candidates", fake_discover_direct
    )
    monkeypatch.setattr("aria_core.token_absorber.absorb", fake_absorb_standard)

    full = await ba.run_bonding_discovery_cycle(limit_per_launchpad=10)
    assert full["bonding"] == {"kept": 1}
    assert full["direct"] == {"kept": 1, "rejected": 1}


@pytest.mark.asyncio
async def test_run_bonding_discovery_cycle_direct_failure_does_not_erase_bonding(monkeypatch):
    async def fake_discover_and_absorb_bonding(*, limit_per_launchpad=50):
        return {"kept": 1}

    def _boom(*a, **k):
        raise RuntimeError("launchpad_discovery indisponible")

    monkeypatch.setattr(ba, "discover_and_absorb_bonding", fake_discover_and_absorb_bonding)
    monkeypatch.setattr("aria_core.services.launchpad_discovery.discover_direct_candidates", _boom)

    result = await ba.run_bonding_discovery_cycle()
    assert result["bonding"] == {"kept": 1}
    assert result["direct"] == {}
