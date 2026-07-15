"""Surveillance continue des positions ouvertes + plafond de concentration (#187) --
module isolé de paper_trader.py (aucun accès DB direct ici, tout est injectable/mocké)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aria_core import paper_trader_risk as risk

CONTRACT = "0x" + "a" * 40


# ── derive_category ────────────────────────────────────────────────────────────────

def test_derive_category_uses_launchpad():
    assert risk.derive_category("virtuals_bonding") == "virtuals_bonding"


def test_derive_category_suffixes_bonding_phase():
    assert risk.derive_category("virtuals_bonding", bonding_phase=True) == "virtuals_bonding-bonding"


def test_derive_category_defaults_to_unknown():
    assert risk.derive_category(None) == "unknown"
    assert risk.derive_category("") == "unknown"
    assert risk.derive_category("  ") == "unknown"


# ── category_exposure_usd ──────────────────────────────────────────────────────────

def test_category_exposure_sums_matching_positions_only():
    opens = [
        {"category": "clanker", "cost_usd": 50_000},
        {"category": "virtuals_bonding", "cost_usd": 30_000},
        {"category": "clanker", "cost_usd": 20_000},
    ]
    assert risk.category_exposure_usd("clanker", opens) == 70_000
    assert risk.category_exposure_usd("virtuals_bonding", opens) == 30_000
    assert risk.category_exposure_usd("unknown", opens) == 0.0


def test_category_exposure_empty_category_is_zero():
    assert risk.category_exposure_usd("", [{"category": "clanker", "cost_usd": 50_000}]) == 0.0


# ── fit_alloc_to_concentration_cap ─────────────────────────────────────────────────

def test_fit_alloc_unaffected_when_no_category():
    fitted = risk.fit_alloc_to_concentration_cap(
        category="", alloc=50_000, already_deployed_usd=0, starting_capital=1_000_000, min_alloc=10_000,
    )
    assert fitted == 50_000


def test_fit_alloc_unaffected_under_cap():
    # 40% de 1M = 400k déjà -- 0 déployé, 50k tient largement en dessous.
    fitted = risk.fit_alloc_to_concentration_cap(
        category="clanker", alloc=50_000, already_deployed_usd=0, starting_capital=1_000_000, min_alloc=10_000,
    )
    assert fitted == 50_000


def test_fit_alloc_reduced_to_remaining_room():
    # Plafond 400k, déjà 380k déployés -- room = 20k, alloc demandée 50k -> réduite à 20k.
    fitted = risk.fit_alloc_to_concentration_cap(
        category="clanker", alloc=50_000, already_deployed_usd=380_000, starting_capital=1_000_000, min_alloc=5_000,
    )
    assert fitted == 20_000


def test_fit_alloc_skipped_when_room_below_minimum():
    # Room = 2k, min_alloc = 5k -> trop peu pour une position significative, skip (0.0).
    fitted = risk.fit_alloc_to_concentration_cap(
        category="clanker", alloc=50_000, already_deployed_usd=398_000, starting_capital=1_000_000, min_alloc=5_000,
    )
    assert fitted == 0.0


def test_fit_alloc_zero_when_cap_already_reached():
    fitted = risk.fit_alloc_to_concentration_cap(
        category="clanker", alloc=50_000, already_deployed_usd=400_000, starting_capital=1_000_000, min_alloc=1_000,
    )
    assert fitted == 0.0


# ── EntrySecuritySnapshot ──────────────────────────────────────────────────────────

def test_entry_snapshot_json_round_trip():
    snap = risk.EntrySecuritySnapshot(
        is_honeypot=False, cannot_sell=False, hidden_owner=False,
        can_take_back_ownership=False, contract_verified=True, owner_address="0x" + "0" * 40,
    )
    restored = risk.EntrySecuritySnapshot.from_json(snap.to_json())
    assert restored == snap


def test_entry_snapshot_from_json_handles_garbage():
    assert risk.EntrySecuritySnapshot.from_json(None) is None
    assert risk.EntrySecuritySnapshot.from_json("") is None
    assert risk.EntrySecuritySnapshot.from_json("not json") is None
    assert risk.EntrySecuritySnapshot.from_json("[1, 2]") is None


@dataclass
class FakeCtx:
    is_honeypot: bool | None = None
    cannot_sell: bool | None = None
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    contract_verified: bool | None = None


@pytest.mark.asyncio
async def test_capture_entry_snapshot_reuses_ctx_and_adds_owner(monkeypatch):
    from aria_core.services import blockscout as bs

    async def fake_read_owner(address):
        return "0x" + "1" * 40, None

    # Patch l'INSTANCE singleton (bs.blockscout_client), pas la classe -- un patch au
    # niveau classe peut être masqué en permanence par un patch au niveau instance fait
    # par un AUTRE fichier de test plus tôt dans la suite (gotcha connu de
    # monkeypatch.setattr sur un attribut qui ne résout QUE via la classe : au undo, il
    # se restaure comme attribut d'INSTANCE, ce qui masque tout futur patch de classe).
    monkeypatch.setattr(bs.blockscout_client, "read_owner", fake_read_owner)

    ctx = FakeCtx(is_honeypot=False, cannot_sell=False, hidden_owner=False,
                  can_take_back_ownership=False, contract_verified=True)
    snap = await risk.capture_entry_snapshot(CONTRACT, ctx)
    assert snap.is_honeypot is False
    assert snap.contract_verified is True
    assert snap.owner_address == "0x" + "1" * 40


# ── rescan_open_position ───────────────────────────────────────────────────────────

def _position(**overrides) -> dict:
    base = {
        "contract": CONTRACT,
        "entry_security_json": risk.EntrySecuritySnapshot(
            is_honeypot=False, cannot_sell=False, hidden_owner=False,
            can_take_back_ownership=False, contract_verified=True,
            owner_address="0x" + "0" * 40,  # renoncée à l'entrée
        ).to_json(),
    }
    base.update(overrides)
    return base


@dataclass
class FakeSecurity:
    available: bool = True
    is_honeypot: bool | None = False
    cannot_sell_all: bool | None = False
    hidden_owner: bool | None = False
    can_take_back_ownership: bool | None = False


@dataclass
class FakeFlags:
    available: bool = True
    is_verified: bool | None = True


def _patch_clients(monkeypatch, *, security=None, flags=None, owner=("0x" + "0" * 40, None)):
    from aria_core.services import goplus as gp
    from aria_core.services import blockscout as bs

    security = security or FakeSecurity()
    flags = flags or FakeFlags()

    async def fake_get_token_security(address, **kw):
        return security

    async def fake_check_contract_flags(address):
        return flags

    async def fake_read_owner(address):
        return owner

    # Patch les INSTANCES singleton (gp.goplus_client/bs.blockscout_client), pas les
    # classes -- voir le commentaire de test_capture_entry_snapshot_reuses_ctx_and_adds_owner
    # sur le gotcha monkeypatch classe-vs-instance qui rend un patch de classe silencieusement
    # sans effet si un AUTRE fichier de test a déjà patché l'instance plus tôt dans la suite.
    monkeypatch.setattr(gp.goplus_client, "get_token_security", fake_get_token_security)
    monkeypatch.setattr(bs.blockscout_client, "check_contract_flags", fake_check_contract_flags)
    monkeypatch.setattr(bs.blockscout_client, "read_owner", fake_read_owner)


@pytest.mark.asyncio
async def test_rescan_no_baseline_returns_none():
    """Position ouverte avant #187 (pas d'entry_security_json) -- pas de référence, on
    ne fabrique jamais un signal."""
    assert await risk.rescan_open_position({"contract": CONTRACT}) is None


@pytest.mark.asyncio
async def test_rescan_nothing_new_returns_none(monkeypatch):
    _patch_clients(monkeypatch)
    assert await risk.rescan_open_position(_position()) is None


@pytest.mark.asyncio
async def test_rescan_detects_new_honeypot(monkeypatch):
    _patch_clients(monkeypatch, security=FakeSecurity(is_honeypot=True))
    result = await risk.rescan_open_position(_position())
    assert result is not None
    assert any("honeypot" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_rescan_ignores_honeypot_already_true_at_entry(monkeypatch):
    """Un honeypot déjà présent à l'entrée n'est PAS un signal NOUVEAU -- ne doit pas
    déclencher (même si ça n'aurait jamais dû être ouvert, ce n'est pas le rôle de ce
    mécanisme de re-juger la décision d'entrée)."""
    _patch_clients(monkeypatch, security=FakeSecurity(is_honeypot=True))
    pos = _position(entry_security_json=risk.EntrySecuritySnapshot(is_honeypot=True).to_json())
    assert await risk.rescan_open_position(pos) is None


@pytest.mark.asyncio
async def test_rescan_detects_ownership_recaptured(monkeypatch):
    _patch_clients(monkeypatch, owner=("0x" + "9" * 40, None))
    result = await risk.rescan_open_position(_position())
    assert result is not None
    assert any("ownership repris" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_rescan_no_ownership_flag_when_never_renounced(monkeypatch):
    """Owner déjà non-renoncé à l'entrée (ex. contrat lambda sans info) -- un owner
    inchangé ne doit pas déclencher un faux positif."""
    _patch_clients(monkeypatch, owner=("0x" + "9" * 40, None))
    pos = _position(
        entry_security_json=risk.EntrySecuritySnapshot(owner_address="0x" + "9" * 40).to_json()
    )
    assert await risk.rescan_open_position(pos) is None


@pytest.mark.asyncio
async def test_rescan_detects_verification_lost(monkeypatch):
    _patch_clients(monkeypatch, flags=FakeFlags(is_verified=False))
    result = await risk.rescan_open_position(_position())
    assert result is not None
    assert any("plus vérifié" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_rescan_tolerates_goplus_failure(monkeypatch):
    from aria_core.services import goplus as gp

    async def failing_security(address, **kw):
        raise RuntimeError("boom")

    _patch_clients(monkeypatch)
    monkeypatch.setattr(gp.goplus_client, "get_token_security", failing_security)

    # Ne doit jamais lever -- dégrade juste ce sous-signal.
    result = await risk.rescan_open_position(_position())
    assert result is None


# ── dépeg USDC ──────────────────────────────────────────────────────────────────────

@dataclass
class FakePriceResult:
    available: bool
    prices: dict


@pytest.mark.asyncio
async def test_usdc_depeg_pct_computes_deviation(monkeypatch):
    from aria_core.services import coingecko as cg

    async def fake_get_simple_price(coin_ids, *, vs_currencies=None):
        return FakePriceResult(available=True, prices={"usd-coin": {"usd": 0.97}})

    monkeypatch.setattr(cg.coingecko_client, "get_simple_price", fake_get_simple_price)
    pct = await risk.usdc_depeg_pct()
    assert round(pct, 3) == 0.03


@pytest.mark.asyncio
async def test_usdc_depeg_pct_none_when_unavailable(monkeypatch):
    from aria_core.services import coingecko as cg

    async def fake_get_simple_price(coin_ids, *, vs_currencies=None):
        return FakePriceResult(available=False, prices={})

    monkeypatch.setattr(cg.coingecko_client, "get_simple_price", fake_get_simple_price)
    assert await risk.usdc_depeg_pct() is None


@pytest.mark.asyncio
async def test_is_usdc_depegged_threshold(monkeypatch):
    from aria_core.services import coingecko as cg

    async def fake_get_simple_price(coin_ids, *, vs_currencies=None):
        return FakePriceResult(available=True, prices={"usd-coin": {"usd": 0.985}})  # 1.5% écart

    monkeypatch.setattr(cg.coingecko_client, "get_simple_price", fake_get_simple_price)
    assert await risk.is_usdc_depegged() is True


@pytest.mark.asyncio
async def test_is_usdc_not_depegged_within_threshold(monkeypatch):
    from aria_core.services import coingecko as cg

    async def fake_get_simple_price(coin_ids, *, vs_currencies=None):
        return FakePriceResult(available=True, prices={"usd-coin": {"usd": 0.996}})  # 0.4% écart

    monkeypatch.setattr(cg.coingecko_client, "get_simple_price", fake_get_simple_price)
    assert await risk.is_usdc_depegged() is False
