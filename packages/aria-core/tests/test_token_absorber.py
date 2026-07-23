"""Absorbeur de tokens — garder / rejeter-pour-toujours / ressusciter (DB isolée)."""
from __future__ import annotations

import time

import pytest

from aria_core import screened_pool as sp
from aria_core import token_absorber as ta
from aria_core.services.blockscout import AddressInfo
from aria_core.skills import liquidity_stability
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "absorb_test.db"))
    # 22/07 -- item #19 : liquidity_stability.py a son PROPRE DB_PATH (module séparé),
    # même piège d'isolation que screened_pool ci-dessus -- sans ça, `absorb()` de
    # test écrirait dans la vraie base par défaut et polluerait les tests suivants.
    monkeypatch.setattr(liquidity_stability, "DB_PATH", str(tmp_path / "vc_liquidity_test.db"))
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
    """Mauvais acteur CONFIRMÉ (blacklist active) -- pas juste des aspects
    d'investissement faibles (liquidité/vérification/concentration), qui sont
    devenus des échecs MOUS depuis le 10/07 (cf. test_safety_screen.py)."""
    return TokenScanContext(
        contract=contract, valid_address=True,
        best_pair=PairSnapshot(pair_address="0xpool", liquidity_usd=800.0, base_symbol="RUG"),
        security_score=20, lite_verdict="DANGER",
        contract_verified=False, has_mint=True, top_holder_pct=80.0,
        has_blacklist=True,
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
async def test_soft_fail_persists_real_score_and_liquidity():
    """15/07 (#158) : un échec mou APRÈS un scan complet doit persister le vrai
    score/liquidité/verdict -- avant ce correctif, un candidat pending prometteur
    (score 78, liquidité 50k) était indiscernable d'un candidat sans aucun signal
    (0/0 codé en dur)."""
    ctx = _clean_ctx("0xpromising")
    ctx.top_holder_pct = None
    scan = _scanner({"0xpromising": ctx})
    assert await ta.absorb("0xpromising", scanner=scan) == "skip_incomplete"
    row = (await sp.list_pool(status="pending"))[0]
    assert row["liquidity_usd"] == 50_000.0
    assert row["security_score"] == 78
    assert row["verdict"] == "SAFE"


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
async def test_source_is_stored_on_kept():
    scan = _scanner({"0xgood": _clean_ctx("0xgood")})
    assert await ta.absorb("0xgood", scanner=scan, source="top_pools") == "kept"
    row = (await sp.list_pool())[0]
    assert row["source"] == "top_pools"


@pytest.mark.asyncio
async def test_source_is_stored_on_pending():
    ctx = _clean_ctx("0xunknown")
    ctx.top_holder_pct = None
    scan = _scanner({"0xunknown": ctx})
    assert await ta.absorb("0xunknown", scanner=scan, source="radar_x") == "skip_incomplete"
    row = (await sp.list_pool(status="pending"))[0]
    assert row["source"] == "radar_x"


@pytest.mark.asyncio
async def test_source_is_stored_on_rejected():
    scan = _scanner({"0xrug": _scam_ctx("0xrug")})
    assert await ta.absorb("0xrug", scanner=scan, source="bonding_direct") == "rejected"
    row = (await sp.list_pool(status="rejected"))[0]
    assert row["source"] == "bonding_direct"


@pytest.mark.asyncio
async def test_source_defaults_to_empty_string():
    scan = _scanner({"0xgood": _clean_ctx("0xgood")})
    assert await ta.absorb("0xgood", scanner=scan) == "kept"
    row = (await sp.list_pool())[0]
    assert row["source"] == ""


@pytest.mark.asyncio
async def test_reconsider_on_signal_passes_source_through():
    rug, good = _scam_ctx("0xtok"), _clean_ctx("0xtok")
    await ta.absorb("0xtok", scanner=_scanner({"0xtok": rug}))
    verdict = await ta.reconsider_on_signal(
        "0xtok", scanner=_scanner({"0xtok": good}), source="radar_x"
    )
    assert verdict == "kept"
    row = (await sp.list_pool())[0]
    assert row["source"] == "radar_x"


@pytest.mark.asyncio
async def test_within_max_age_is_classified_normally():
    now_ms = time.time() * 1000
    fresh_ctx = _clean_ctx("0xfresh")
    fresh_ctx.best_pair.pair_created_at = int(now_ms - 10 * 86_400_000)  # 10 jours
    scan = _scanner({"0xfresh": fresh_ctx})
    assert await ta.absorb("0xfresh", scanner=scan, max_age_days=182) == "kept"


# --- Volet C (12/07) : pré-filtre découverte Blockscout léger avant scan complet ---

def _boom_scan(contract, **kw):
    raise AssertionError("le scan complet ne doit pas être invoqué : le pré-filtre aurait dû court-circuiter")


def _info_returning(info):
    async def _get(_self, addr):
        return info
    return _get


@pytest.mark.asyncio
async def test_prefilter_skips_full_scan_when_unverified_and_no_holders(monkeypatch):
    info = AddressInfo(address="0xstale", is_verified=False, holders_count=0, available=True)
    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _info_returning(info))

    verdict = await ta.absorb("0xstale", scanner=_boom_scan, known_age_days=5.0)

    assert verdict == "skip_prefiltered"
    row = (await sp.list_pool(status="pending"))[0]
    assert "pré-filtre découverte" in row["screen_reason"]
    assert "non vérifié" in row["screen_reason"]
    assert "holders non indexés" in row["screen_reason"]


@pytest.mark.asyncio
async def test_prefilter_records_source(monkeypatch):
    info = AddressInfo(address="0xstale", is_verified=False, holders_count=None, available=True)
    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _info_returning(info))

    await ta.absorb("0xstale", scanner=_boom_scan, known_age_days=5.0, source="top_pools")

    row = (await sp.list_pool(status="pending"))[0]
    assert row["source"] == "top_pools"


@pytest.mark.asyncio
async def test_prefilter_guard_rail_skips_fresh_candidate(monkeypatch):
    """Un candidat de <2j ne doit JAMAIS être pré-filtré, même si Blockscout le dirait
    non vérifié -- il n'a simplement pas encore eu le temps de mûrir (faux négatif)."""
    async def _boom_blockscout(_self, addr):
        raise AssertionError("le pré-filtre ne doit pas consulter Blockscout sous le seuil d'âge")

    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _boom_blockscout)
    ctx = _clean_ctx("0xfresh2")
    scan = _scanner({"0xfresh2": ctx})

    verdict = await ta.absorb("0xfresh2", scanner=scan, known_age_days=0.5)

    assert verdict == "kept"


@pytest.mark.asyncio
async def test_known_age_days_none_never_prefilters(monkeypatch):
    async def _boom_blockscout(_self, addr):
        raise AssertionError("known_age_days=None (défaut) ne doit jamais déclencher le pré-filtre")

    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _boom_blockscout)
    ctx = _clean_ctx("0xnoage")
    scan = _scanner({"0xnoage": ctx})

    verdict = await ta.absorb("0xnoage", scanner=scan)

    assert verdict == "kept"


@pytest.mark.asyncio
async def test_prefilter_fails_open_when_blockscout_unavailable(monkeypatch):
    info = AddressInfo(address="0xdown", available=False, error="donnée on-chain indisponible")
    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _info_returning(info))
    ctx = _clean_ctx("0xdown")
    scan = _scanner({"0xdown": ctx})

    verdict = await ta.absorb("0xdown", scanner=scan, known_age_days=5.0)

    assert verdict == "kept"


@pytest.mark.asyncio
async def test_prefilter_lets_verified_candidate_through(monkeypatch):
    info = AddressInfo(address="0xok", is_verified=True, holders_count=50, available=True)
    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _info_returning(info))
    ctx = _clean_ctx("0xok")
    scan = _scanner({"0xok": ctx})

    verdict = await ta.absorb("0xok", scanner=scan, known_age_days=5.0)

    assert verdict == "kept"


@pytest.mark.asyncio
async def test_prefilter_skipped_when_ctx_already_provided(monkeypatch):
    """``ctx`` déjà fourni (ex. bonding_direct) : le scan a déjà eu lieu, aucune
    économie possible -- le pré-filtre ne doit pas être consulté du tout."""
    async def _boom_blockscout(_self, addr):
        raise AssertionError("ctx déjà fourni : le pré-filtre ne doit jamais être consulté")

    monkeypatch.setattr(type(ta.blockscout_client), "get_address_info", _boom_blockscout)

    verdict = await ta.absorb("0xpreset", scanner=_boom_scan, known_age_days=5.0, ctx=_clean_ctx("0xpreset"))

    assert verdict == "kept"


# ── Confirmation de stabilité temporelle sur la liquidité (22/07, item #19) ─────


@pytest.mark.asyncio
async def test_first_scan_kept_even_though_stability_unconfirmed():
    """Premier scan d'un contrat -- aucun antécédent, jamais un rejet sur une
    absence de comparaison (même doctrine fail-open que le reste du projet)."""
    ctx = _clean_ctx("0xfirstscan")
    verdict = await ta.absorb("0xfirstscan", scanner=_scanner({"0xfirstscan": ctx}))
    assert verdict == "kept"


@pytest.mark.asyncio
async def test_stable_liquidity_across_rescans_stays_kept():
    ctx1 = _clean_ctx("0xstable")
    ctx2 = _clean_ctx("0xstable")  # même liquidité (50_000.0)
    await ta.absorb("0xstable", scanner=_scanner({"0xstable": ctx1}))
    verdict = await ta.absorb("0xstable", scanner=_scanner({"0xstable": ctx2}), force=True)
    assert verdict == "kept"


@pytest.mark.asyncio
async def test_liquidity_drop_between_scans_is_rejected_soft():
    """Une chute de liquidité suspecte entre deux scans du MÊME contrat -- soft-fail
    (pending, jamais 'rejected pour toujours', comportement de marché)."""
    ctx_high = _clean_ctx("0xdrop")  # liquidité 50_000.0
    ctx_low = TokenScanContext(
        contract="0xdrop", valid_address=True,
        best_pair=PairSnapshot(pair_address="0xpool", liquidity_usd=10_000.0, base_symbol="GOOD"),
        security_score=78, lite_verdict="SAFE",
        contract_verified=True, has_mint=False, has_blacklist=False,
        has_disable_transfers=False, top_holder_pct=12.0,
    )
    await ta.absorb("0xdrop", scanner=_scanner({"0xdrop": ctx_high}))
    verdict = await ta.absorb("0xdrop", scanner=_scanner({"0xdrop": ctx_low}), force=True)
    assert verdict != "kept"
    assert await sp.get_status("0xdrop") != "rejected"  # soft-fail, jamais 'pour toujours'
