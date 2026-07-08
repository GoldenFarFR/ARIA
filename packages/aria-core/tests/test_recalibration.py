"""Requêtes de recalibrage : ARIA escalade un token prometteur mais opaque."""
from __future__ import annotations

import aria_core.recalibration as rc
import pytest
from aria_core.recalibration import assess_transparency, is_promising
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DB_PATH", str(tmp_path / "recal.db"))


def _ctx(**kw) -> TokenScanContext:
    ctx = TokenScanContext(contract="0x" + "a" * 40, valid_address=True)
    ctx.contract_verified = True
    ctx.top_holder_pct = 12.0
    ctx.security_score = 60
    ctx.best_pair = PairSnapshot(pair_address="0x" + "b" * 40, liquidity_usd=40_000, base_symbol="TKN")
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


# ── Transparence (pur) ──────────────────────────────────────────────────────

def test_fully_transparent():
    assert assess_transparency(_ctx()).transparent is True


def test_unverified_is_opaque():
    v = assess_transparency(_ctx(contract_verified=False))
    assert v.transparent is False
    assert any("vérifié" in m for m in v.missing)


def test_unknown_mint_authority_is_opaque():
    v = assess_transparency(_ctx(has_mint=True, mint_authority="unknown"))
    assert v.transparent is False
    assert any("autorité du mint" in m for m in v.missing)


def test_resolved_mint_authority_is_transparent():
    # mint présent MAIS autorité résolue (launchpad) -> transparent
    assert assess_transparency(_ctx(has_mint=True, mint_authority="launchpad")).transparent is True


def test_unknown_holders_is_opaque():
    v = assess_transparency(_ctx(top_holder_pct=None))
    assert v.transparent is False
    assert any("holders" in m for m in v.missing)


# ── Promesse ────────────────────────────────────────────────────────────────

def test_dust_is_not_promising():
    assert is_promising(_ctx(best_pair=PairSnapshot(liquidity_usd=500))) is False


def test_no_pair_is_not_promising():
    assert is_promising(_ctx(best_pair=None)) is False


def test_liquid_token_is_promising():
    assert is_promising(_ctx()) is True


# ── Escalade + file d'attente ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalates_promising_but_opaque():
    ctx = _ctx(contract_verified=False)  # prometteur (liq 40k) mais opaque
    created = await rc.maybe_escalate(ctx, symbol="TKN")
    assert created is True
    assert await rc.count_pending() == 1
    pend = await rc.list_pending()
    assert pend[0]["contract"] == ctx.contract
    assert any("vérifié" in m for m in pend[0]["missing"])


@pytest.mark.asyncio
async def test_does_not_escalate_transparent_token():
    assert await rc.maybe_escalate(_ctx()) is False
    assert await rc.count_pending() == 0


@pytest.mark.asyncio
async def test_does_not_escalate_dust():
    ctx = _ctx(contract_verified=False, best_pair=PairSnapshot(liquidity_usd=300))
    assert await rc.maybe_escalate(ctx) is False
    assert await rc.count_pending() == 0


@pytest.mark.asyncio
async def test_escalation_is_idempotent():
    ctx = _ctx(contract_verified=False)
    assert await rc.maybe_escalate(ctx) is True
    assert await rc.maybe_escalate(ctx) is False  # déjà en attente
    assert await rc.count_pending() == 1


@pytest.mark.asyncio
async def test_resolve_clears_pending():
    ctx = _ctx(contract_verified=False)
    await rc.maybe_escalate(ctx)
    await rc.resolve_request(ctx.contract, resolution="launchpad Bankr ajouté")
    assert await rc.count_pending() == 0
    # une nouvelle opacité recrée une requête (résolue -> re-pending)
    assert await rc.maybe_escalate(ctx) is True
