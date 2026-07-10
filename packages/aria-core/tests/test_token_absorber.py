"""Absorbeur de tokens — garder / rejeter-pour-toujours / ressusciter (DB isolée)."""
from __future__ import annotations

import time

import pytest

from aria_core import screened_pool as sp
from aria_core import token_absorber as ta
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "absorb_test.db"))
    yield


def _clean_ctx(contract: str) -> TokenScanContext:
    return TokenScanContext(
        contract=contract, valid_address=True,
        best_pair=PairSnapshot(pair_address="0xpool", liquidity_usd=50_000.0, base_symbol="GOOD"),
        security_score=78, lite_verdict="SAFE",
        contract_verified=True, has_mint=False, has_blacklist=False,
        has_disable_transfers=False, top_holder_pct=12.0,
    )


def _scam_ctx(contract: str) -> TokenScanContext:
    return TokenScanContext(
        contract=contract, valid_address=True,
        best_pair=PairSnapshot(pair_address="0xpool", liquidity_usd=800.0, base_symbol="RUG"),
        security_score=20, lite_verdict="DANGER",
        contract_verified=False, has_mint=True, top_holder_pct=80.0,
    )


def _scanner(ctx_by_contract):
    async def _scan(contract, **kw):
        return ctx_by_contract[contract]
    return _scan


@pytest.mark.asyncio
async def test_absorb_with_preset_ctx_skips_internal_scan():
    """``ctx=`` (10/07, évite un double scan réseau depuis
    ``bonding_absorber.absorb_direct_candidate``) : le scanner injecté ne doit
    JAMAIS être appelé quand un contexte déjà scanné est fourni."""
    async def _boom(contract, **kw):
        raise AssertionError("ne doit pas re-scanner si ctx est déjà fourni")

    verdict = await ta.absorb("0xgood", scanner=_boom, ctx=_clean_ctx("0xgood"))
    assert verdict == "kept"
    assert await sp.get_status("0xgood") == "active"


@pytest.mark.asyncio
async def test_real_value_is_kept():
    scan = _scanner({"0xgood": _clean_ctx("0xgood")})
    assert await ta.absorb("0xgood", scanner=scan) == "kept"
    assert await sp.get_status("0xgood") == "active"
    pool = await sp.list_pool()
    assert pool[0]["symbol"] == "GOOD"
    assert "screené" in (pool[0]["screen_reason"] or "")


@pytest.mark.asyncio
async def test_junk_is_rejected_forever():
    scan = _scanner({"0xrug": _scam_ctx("0xrug")})
    assert await ta.absorb("0xrug", scanner=scan) == "rejected"
    assert await sp.get_status("0xrug") == "rejected"
    # 2e passage : jeté pour toujours, pas re-scanné.
    assert await ta.absorb("0xrug", scanner=scan) == "skip_rejected"


@pytest.mark.asyncio
async def test_active_is_not_rescanned():
    scan = _scanner({"0xgood": _clean_ctx("0xgood")})
    await ta.absorb("0xgood", scanner=scan)
    assert await ta.absorb("0xgood", scanner=scan) == "skip_active"


@pytest.mark.asyncio
async def test_resurrection_on_signal_reevaluates():
    # D'abord rejeté (rien) ; puis le projet reprend vie (contexte propre) + un bruit.
    rug, good = _scam_ctx("0xtok"), _clean_ctx("0xtok")
    assert await ta.absorb("0xtok", scanner=_scanner({"0xtok": rug})) == "rejected"
    # Un bruit réapparaît -> résurrection -> réévaluation sur les nouveaux faits.
    verdict = await ta.reconsider_on_signal("0xtok", scanner=_scanner({"0xtok": good}))
    assert verdict == "kept"
    assert await sp.get_status("0xtok") == "active"


@pytest.mark.asyncio
async def test_resurrection_still_rejects_if_still_junk():
    rug = _scam_ctx("0xtok")
    await ta.absorb("0xtok", scanner=_scanner({"0xtok": rug}))
    # Le bruit réveille, mais les faits sont toujours mauvais -> re-rejeté.
    verdict = await ta.reconsider_on_signal("0xtok", scanner=_scanner({"0xtok": rug}))
    assert verdict == "rejected"
    assert await sp.get_status("0xtok") == "rejected"


@pytest.mark.asyncio
async def test_older_than_max_age_is_skipped_before_security_screen():
    now_ms = time.time() * 1000
    old_ctx = _clean_ctx("0xold")
    old_ctx.best_pair.pair_created_at = int(now_ms - 400 * 86_400_000)  # ~400 jours
    scan_calls: list[str] = []

    async def scan(contract, **kw):
        scan_calls.append(contract)
        return old_ctx

    verdict = await ta.absorb("0xold", scanner=scan, max_age_days=182)
    assert verdict == "skip_too_old"
    # Ni gardé ni rejeté : hors-scope, jamais écrit dans le pool.
    assert await sp.get_status("0xold") is None
    assert scan_calls == ["0xold"]


@pytest.mark.asyncio
async def test_soft_fail_leaves_a_pending_trace_with_reason():
    # Holders inconnus (top_holder_pct=None) : echec MOU (pas hard_fail) -> avant le
    # correctif #77, aucune trace nulle part (ni pool, ni raison). Desormais :
    # status='pending' + la vraie raison, consultable.
    ctx = _clean_ctx("0xunknown")
    ctx.top_holder_pct = None
    scan = _scanner({"0xunknown": ctx})
    assert await ta.absorb("0xunknown", scanner=scan) == "skip_incomplete"
    assert await sp.get_status("0xunknown") == "pending"
    row = (await sp.list_pool(status="pending"))[0]
    assert "holder" in row["screen_reason"].lower()


@pytest.mark.asyncio
async def test_soft_fail_pending_is_still_rescanned_next_cycle():
    # 'pending' ne doit PAS court-circuiter comme 'rejected'/'active' : le prochain
    # cycle doit re-scanner normalement (c'est tout le point d'un echec mou).
    ctx_unknown = _clean_ctx("0xretry")
    ctx_unknown.top_holder_pct = None
    ctx_good = _clean_ctx("0xretry")
    assert await ta.absorb("0xretry", scanner=_scanner({"0xretry": ctx_unknown})) == "skip_incomplete"
    assert await ta.absorb("0xretry", scanner=_scanner({"0xretry": ctx_good})) == "kept"
    assert await sp.get_status("0xretry") == "active"


@pytest.mark.asyncio
async def test_within_max_age_is_classified_normally():
    now_ms = time.time() * 1000
    fresh_ctx = _clean_ctx("0xfresh")
    fresh_ctx.best_pair.pair_created_at = int(now_ms - 10 * 86_400_000)  # 10 jours
    scan = _scanner({"0xfresh": fresh_ctx})
    assert await ta.absorb("0xfresh", scanner=scan, max_age_days=182) == "kept"
