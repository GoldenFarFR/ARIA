"""Absorbeur niche bonding (DB isolée) — jamais network='base' (pool 85% VC)."""
from __future__ import annotations

import pytest

from aria_core import screened_pool as sp
from aria_core.skills import bonding_absorber as ba
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext


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


# ── absorb_direct_candidate (10/07, correctif dry-run) ────────────

def _direct_ctx(contract: str, *, has_pair: bool = True, **overrides) -> TokenScanContext:
    """Candidat 'direct' fraîchement découvert (Clanker/Virtuals gradués)."""
    defaults = dict(
        contract=contract, valid_address=True,
        best_pair=(
            PairSnapshot(
                pair_address="0xpair", dex_id="aerodrome", liquidity_usd=50_000.0,
                volume_24h_usd=10_000.0, base_symbol="TOK", quote_symbol="WETH",
            )
            if has_pair else None
        ),
        security_score=78, lite_verdict="SAFE", contract_verified=True,
        has_mint=False, has_blacklist=False, has_disable_transfers=False,
        top_holder_pct=12.0,
    )
    defaults.update(overrides)
    return TokenScanContext(**defaults)


@pytest.mark.asyncio
async def test_absorb_direct_candidate_no_pair_yet_goes_pending_not_rejected():
    """Le bug diagnostiqué en direct (10/07) : 18/20 candidats 'direct' bannis À VIE
    sur le seul signal 'pas de paire DEX' -- un token tout juste déployé n'a
    souvent pas encore de paire indexée. Doit atterrir en 'pending', jamais
    'rejected'."""
    scan = _scanner({"0xfresh": _direct_ctx("0xfresh", has_pair=False)})
    assert await ba.absorb_direct_candidate("0xfresh", scanner=scan) == "skip_incomplete"
    assert await sp.get_status("0xfresh") == "pending"


@pytest.mark.asyncio
async def test_absorb_direct_candidate_with_pair_delegates_to_standard_pipeline():
    scan = _scanner({"0xmature": _direct_ctx("0xmature", has_pair=True)})
    assert await ba.absorb_direct_candidate("0xmature", scanner=scan) == "kept"
    # Rejoint le pool STANDARD (network="base"), pas le pool bonding.
    assert await sp.count_pool("active") == 1
    assert await sp.count_pool("active", network=ba.BONDING_NETWORK) == 0


@pytest.mark.asyncio
async def test_absorb_direct_candidate_real_hard_fail_still_rejected_forever():
    """Une paire EXISTE mais le token est un mauvais acteur confirmé (blacklist) :
    le rejet définitif standard s'applique toujours -- seule l'absence de paire
    bénéficie de la grâce."""
    scan = _scanner({"0xrug": _direct_ctx("0xrug", has_pair=True, has_blacklist=True)})
    assert await ba.absorb_direct_candidate("0xrug", scanner=scan) == "rejected"
    assert await sp.get_status("0xrug") == "rejected"


@pytest.mark.asyncio
async def test_absorb_direct_candidate_already_rejected_short_circuits():
    async def _boom(contract, **kw):
        raise AssertionError("ne doit pas re-scanner un contrat déjà rejeté")

    scan = _scanner({"0xrug": _direct_ctx("0xrug", has_pair=True, has_blacklist=True)})
    assert await ba.absorb_direct_candidate("0xrug", scanner=scan) == "rejected"
    assert await ba.absorb_direct_candidate("0xrug", scanner=_boom) == "skip_rejected"


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

    async def fake_absorb_direct(contract):
        return "kept" if contract == "0xdirect1" else "rejected"

    monkeypatch.setattr(ba, "discover_and_absorb_bonding", fake_discover_and_absorb_bonding)
    monkeypatch.setattr(
        "aria_core.services.launchpad_discovery.discover_direct_candidates", fake_discover_direct
    )
    monkeypatch.setattr(ba, "absorb_direct_candidate", fake_absorb_direct)

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
