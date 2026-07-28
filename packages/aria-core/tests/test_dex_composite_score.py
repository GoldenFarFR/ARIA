"""dex_composite_score.py -- additive DEX security/conviction score (Item #177,
28/07; binary contract-risk pillar + 35% neutral base, 28/07 2nd pass).
Aucun appel réseau réel, tout est mocké."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aria_core import dex_composite_score as dcs
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.goplus import TokenSecurity


def _security(**overrides) -> TokenSecurity:
    base = dict(address="0x" + "a" * 40, available=True)
    base.update(overrides)
    return TokenSecurity(**base)


def _pair(**overrides) -> PairSnapshot:
    base = dict(liquidity_usd=50_000.0, market_cap_usd=100_000.0, pair_address="0x" + "b" * 40)
    base.update(overrides)
    return PairSnapshot(**base)


# ---------------------------------------------------------------------------
# Pillar 1 -- contract/dev residual risk, BINARY (pure function, mint excluded)
# ---------------------------------------------------------------------------
def test_score_contract_risk_none_when_security_unavailable():
    score, reason = dcs._score_contract_risk(None)
    assert score is None
    assert "indisponible" in reason


def test_score_contract_risk_none_when_security_not_available():
    score, reason = dcs._score_contract_risk(_security(available=False))
    assert score is None


def test_score_contract_risk_neutral_base_when_nothing_resolved():
    score, reason = dcs._score_contract_risk(_security())
    assert score == dcs._CONTRACT_RISK_BASE
    assert "base neutre" in reason


def test_score_contract_risk_crashes_to_zero_on_hidden_owner():
    score, reason = dcs._score_contract_risk(_security(hidden_owner=True))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE
    assert "confirmé mauvais" in reason


def test_score_contract_risk_crashes_to_zero_on_can_take_back_ownership():
    score, _ = dcs._score_contract_risk(_security(can_take_back_ownership=True))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE


def test_score_contract_risk_crashes_to_zero_on_slippage_modifiable():
    score, _ = dcs._score_contract_risk(_security(slippage_modifiable=True))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE


def test_score_contract_risk_crashes_to_zero_on_is_blacklisted():
    score, _ = dcs._score_contract_risk(_security(is_blacklisted=True))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE


def test_score_contract_risk_crashes_to_zero_on_confirmed_not_open_source():
    score, _ = dcs._score_contract_risk(_security(is_open_source=False))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE


def test_score_contract_risk_never_penalizes_unknown_open_source():
    """``is_open_source=None`` (unknown) must never be treated like ``False``
    (confirmed unverified) -- fail-open doctrine, stays at the neutral base."""
    score, _ = dcs._score_contract_risk(_security(is_open_source=None))
    assert score == dcs._CONTRACT_RISK_BASE


def test_score_contract_risk_crashes_to_zero_on_high_tax():
    score, reason = dcs._score_contract_risk(_security(buy_tax=0.10, sell_tax=0.10))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE
    assert "confirmé mauvais" in reason


def test_score_contract_risk_ambiguous_low_tax_does_not_move_the_needle():
    """A small, known, non-zero tax (below _TAX_BAD_THRESHOLD_PCT) is neither
    a confirmed-good nor a confirmed-bad signal -- excluded from both counts,
    score stays exactly at the neutral base when it's the only known field."""
    score, _ = dcs._score_contract_risk(_security(buy_tax=0.02, sell_tax=0.0))
    assert score == dcs._CONTRACT_RISK_BASE


def test_score_contract_risk_zero_tax_confirmed_good():
    score, reason = dcs._score_contract_risk(_security(buy_tax=0.0, sell_tax=0.0))
    assert score == dcs._WEIGHT_CONTRACT_RISK
    assert "taxe nulle confirmée" in reason


def test_score_contract_risk_never_goes_below_zero_when_everything_bad():
    score, _ = dcs._score_contract_risk(
        _security(
            hidden_owner=True, can_take_back_ownership=True, slippage_modifiable=True,
            is_blacklisted=True, is_open_source=False, buy_tax=0.5, sell_tax=0.5,
        )
    )
    assert score == 0.0


def test_score_contract_risk_one_bad_signal_dominates_many_good_ones():
    """Binary doctrine, verbatim operator instruction: "aucun malus... peu
    importe combien" de signaux bons a côté -- un seul mauvais confirmé
    écrase tout."""
    score, _ = dcs._score_contract_risk(
        _security(
            hidden_owner=True,  # the one bad signal
            can_take_back_ownership=False, slippage_modifiable=False,
            is_blacklisted=False, is_open_source=True, buy_tax=0.0, sell_tax=0.0,
        )
    )
    assert score == dcs._CONTRACT_RISK_BAD_SCORE


def test_score_contract_risk_scales_proportionally_with_confirmed_good_signals():
    """3 of 6 possible fields resolved, all 3 confirmed good -> bonus_fraction
    = 1.0 -> full weight (matches the operator's "scaler proportionnellement"
    instruction: all RESOLVED fields good means max credit, even if some
    fields stayed unresolved elsewhere)."""
    score, reason = dcs._score_contract_risk(
        _security(hidden_owner=False, can_take_back_ownership=False, slippage_modifiable=False)
    )
    assert score == dcs._WEIGHT_CONTRACT_RISK
    assert "3/3 signaux positifs confirmés" in reason


def test_score_contract_risk_mixed_good_and_none_partial_bonus():
    """1 of 6 fields resolved (good), the rest stay None (unresolved,
    excluded) -- bonus_fraction = 1/1 = 1.0, full credit from that single
    confirmed-good field. Documented known property of this simple formula
    (see the module docstring): with few resolved fields, one positive signal
    can carry disproportionate weight -- acceptable per operator design,
    revisit once dex_score_log.py accumulates real outcomes."""
    score, _ = dcs._score_contract_risk(_security(is_open_source=True))
    assert score == dcs._WEIGHT_CONTRACT_RISK


def test_score_contract_risk_stacks_multiple_confirmed_bad_reasons_in_text():
    score, reason = dcs._score_contract_risk(_security(hidden_owner=True, is_blacklisted=True))
    assert score == dcs._CONTRACT_RISK_BAD_SCORE
    assert "owner caché" in reason
    assert "blacklister" in reason


# ---------------------------------------------------------------------------
# Pillar 1b -- mint authority resolution (binary: bad/good/unresolved)
# ---------------------------------------------------------------------------
async def test_resolve_mint_signal_good_when_confirmed_no_mint():
    state, reason = await dcs._resolve_mint_signal("0xcontract", _security(is_mintable=False))
    assert state == "good"
    assert "pas de fonction mint" in reason


async def test_resolve_mint_signal_unresolved_when_mintable_unknown():
    """``is_mintable=None`` (never checked/unknown) must NOT be treated as a
    confirmed-good "no mint" -- fail-open doctrine, 28/07 2nd pass (differs
    from the old graduated system, which silently assumed "no mint" here)."""
    state, reason = await dcs._resolve_mint_signal("0xcontract", _security(is_mintable=None))
    assert state == "unresolved"


async def test_resolve_mint_signal_unresolved_when_security_unavailable():
    state, _ = await dcs._resolve_mint_signal("0xcontract", _security(available=False))
    assert state == "unresolved"


async def test_resolve_mint_signal_bad_for_eoa_owner(monkeypatch):
    from aria_core.services import blockscout as blockscout_mod
    from aria_core.skills import mint_authority as mint_mod

    @dataclass
    class _Info:
        creator_address: str | None = None
        is_contract: bool | None = None
        available: bool = True

    async def fake_get_address_info(self, address):
        if address == "0xcontract":
            return _Info(creator_address="0xcreator")
        if address == "0xowner":
            return _Info(is_contract=False)
        return _Info(available=False)

    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_address_info", fake_get_address_info)
    monkeypatch.setattr(mint_mod, "match_launchpad", lambda creator: None)

    state, reason = await dcs._resolve_mint_signal(
        "0xcontract", _security(is_mintable=True, owner_address="0xowner"),
    )
    assert state == "bad"
    assert "wallet externe" in reason


async def test_resolve_mint_signal_good_when_launchpad_neutralized(monkeypatch):
    from aria_core.services import blockscout as blockscout_mod
    from aria_core.skills import mint_authority as mint_mod

    @dataclass
    class _Info:
        creator_address: str | None = "0xcreator"
        is_contract: bool | None = None
        available: bool = True

    async def fake_get_address_info(self, address):
        return _Info()

    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_address_info", fake_get_address_info)
    monkeypatch.setattr(mint_mod, "match_launchpad", lambda creator: "Virtuals")

    state, reason = await dcs._resolve_mint_signal("0xcontract", _security(is_mintable=True))
    assert state == "good"
    assert "neutralisé" in reason


async def test_resolve_mint_signal_unresolved_when_authority_indeterminable(monkeypatch):
    from aria_core.services import blockscout as blockscout_mod
    from aria_core.skills import mint_authority as mint_mod

    @dataclass
    class _Info:
        creator_address: str | None = None
        is_contract: bool | None = None
        available: bool = False

    async def fake_get_address_info(self, address):
        return _Info()

    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_address_info", fake_get_address_info)
    monkeypatch.setattr(mint_mod, "match_launchpad", lambda creator: None)

    state, reason = await dcs._resolve_mint_signal("0xcontract", _security(is_mintable=True))
    assert state == "unresolved"
    assert "indéterminable" in reason


# ---------------------------------------------------------------------------
# Pillar 2 -- dev wallet behavior
# ---------------------------------------------------------------------------
async def test_score_dev_behavior_maps_aligned_signal(monkeypatch):
    from aria_core.skills import dev_wallet as dw_mod

    async def fake_facts(contract, creator, *, lp_address=None, client=None, holders=None):
        return dw_mod.DevWalletFacts(creator=creator, available=True)

    monkeypatch.setattr(dw_mod, "gather_dev_wallet_facts", fake_facts)
    monkeypatch.setattr(
        dw_mod, "judge_dev_wallet",
        lambda facts, **kw: dw_mod.DevWalletVerdict(signal="aligned", points=["a acheté"]),
    )

    score, reason = await dcs._score_dev_behavior("0xcontract", _security(owner_address="0xdev"), None)
    assert score == dcs._WEIGHT_DEV_BEHAVIOR
    assert "a acheté" in reason


async def test_score_dev_behavior_maps_concern_signal_to_zero(monkeypatch):
    from aria_core.skills import dev_wallet as dw_mod

    async def fake_facts(contract, creator, *, lp_address=None, client=None, holders=None):
        return dw_mod.DevWalletFacts(creator=creator, available=True)

    monkeypatch.setattr(dw_mod, "gather_dev_wallet_facts", fake_facts)
    monkeypatch.setattr(
        dw_mod, "judge_dev_wallet",
        lambda facts, **kw: dw_mod.DevWalletVerdict(signal="concern", points=["extraction probable"]),
    )

    score, _ = await dcs._score_dev_behavior("0xcontract", _security(owner_address="0xdev"), None)
    assert score == 0.0


async def test_score_dev_behavior_unknown_scores_neutral_35_pct(monkeypatch):
    from aria_core.skills import dev_wallet as dw_mod

    async def fake_facts(contract, creator, *, lp_address=None, client=None, holders=None):
        return dw_mod.DevWalletFacts(creator=None, available=False, error="déployeur inconnu")

    monkeypatch.setattr(dw_mod, "gather_dev_wallet_facts", fake_facts)
    monkeypatch.setattr(
        dw_mod, "judge_dev_wallet",
        lambda facts, **kw: dw_mod.DevWalletVerdict(signal="unknown", points=[facts.error]),
    )

    score, _ = await dcs._score_dev_behavior("0xcontract", _security(available=False), None)
    assert score == pytest.approx(dcs._WEIGHT_DEV_BEHAVIOR * dcs._NEUTRAL_BASE_FRACTION)


# ---------------------------------------------------------------------------
# Pillar 3 -- smart money (generalized)
# ---------------------------------------------------------------------------
async def test_score_smart_money_neutral_when_holders_unavailable():
    @dataclass
    class _Holders:
        available: bool = False

    score, reason = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == pytest.approx(dcs._WEIGHT_SMART_MONEY * dcs._NEUTRAL_BASE_FRACTION)
    assert "holders indisponibles" in reason


async def test_score_smart_money_scales_with_quality_signal(monkeypatch):
    from aria_core.services import smart_money as sm_mod

    @dataclass
    class _Holders:
        available: bool = True

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        return sm_mod.SmartMoneySignal(
            available=True, quality_signal=80.0, smart_wallets=["0xwallet1", "0xwallet2"],
        )

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)

    score, reason = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == pytest.approx(dcs._WEIGHT_SMART_MONEY * 0.8)
    assert "2 wallet(s) convergent(s)" in reason


async def test_score_smart_money_neutral_when_signal_unavailable(monkeypatch):
    from aria_core.services import smart_money as sm_mod

    @dataclass
    class _Holders:
        available: bool = True

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        return sm_mod.SmartMoneySignal(available=False)

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)

    score, reason = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == pytest.approx(dcs._WEIGHT_SMART_MONEY * dcs._NEUTRAL_BASE_FRACTION)
    assert "panne réseau" in reason


async def test_score_smart_money_neutral_when_no_convergence_confirmed(monkeypatch):
    """28/07 audit finding: available=True with quality_signal=None (the
    common case -- 0 or 1 qualified wallet, smart_money.py's own >=2
    convergence gate never crossed) must be labeled distinctly from a real
    data outage, never the same "indisponible" text -- see the comment above
    this branch in ``dex_composite_score._score_smart_money``."""
    from aria_core.services import smart_money as sm_mod

    @dataclass
    class _Holders:
        available: bool = True

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        return sm_mod.SmartMoneySignal(available=True, quality_signal=None)

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)

    score, reason = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == pytest.approx(dcs._WEIGHT_SMART_MONEY * dcs._NEUTRAL_BASE_FRACTION)
    assert "pas de convergence confirmée" in reason
    assert "indisponible" not in reason


async def test_score_smart_money_uses_capped_max_wallets(monkeypatch):
    """28/07, operator go-ahead ('ajouter comme signal supplémentaire') --
    this runs on EVERY BUY candidate (much higher volume than the rare
    parabolic-rescue case smart_money.py's own default was calibrated for),
    so the cap must be lower than _MAX_WALLETS_DEFAULT (8)."""
    from aria_core.services import smart_money as sm_mod

    @dataclass
    class _Holders:
        available: bool = True

    captured = {}

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        captured["max_wallets"] = max_wallets
        return sm_mod.SmartMoneySignal(available=True, quality_signal=50.0)

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)

    await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert captured["max_wallets"] == dcs._MAX_SMART_MONEY_WALLETS
    assert dcs._MAX_SMART_MONEY_WALLETS < 8


# ---------------------------------------------------------------------------
# Pillar 4 -- liquidity/market-cap depth (pure function)
# ---------------------------------------------------------------------------
def test_score_liquidity_depth_neutral_when_market_cap_unknown():
    score, reason = dcs._score_liquidity_depth(50_000.0, None)
    assert score == pytest.approx(dcs._WEIGHT_LIQUIDITY_DEPTH * dcs._NEUTRAL_BASE_FRACTION)
    assert "inconnue" in reason


def test_score_liquidity_depth_full_credit_at_or_above_default_ratio():
    score, _ = dcs._score_liquidity_depth(30_000.0, 100_000.0)  # ratio 0.30
    assert score == pytest.approx(dcs._WEIGHT_LIQUIDITY_DEPTH)


def test_score_liquidity_depth_scales_linearly_below_default_ratio():
    score, reason = dcs._score_liquidity_depth(11_000.0, 100_000.0)  # ratio 0.11
    assert score == pytest.approx(dcs._WEIGHT_LIQUIDITY_DEPTH * (0.11 / 0.30), rel=1e-3)
    assert "11%" in reason or "0.11" in reason or "mince" in reason


def test_score_liquidity_depth_capped_at_full_weight_when_ratio_exceeds_default():
    score, _ = dcs._score_liquidity_depth(90_000.0, 100_000.0)  # ratio 0.90
    assert score == dcs._WEIGHT_LIQUIDITY_DEPTH


# ---------------------------------------------------------------------------
# compute_dex_composite_score -- integration
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_all_pillars_neutral(monkeypatch):
    """Default: every pillar resolves neutrally with no network call --
    individual tests below override what they need to exercise."""
    from aria_core.services import blockscout as blockscout_mod
    from aria_core.skills import dev_wallet as dw_mod
    from aria_core.services import smart_money as sm_mod

    @dataclass
    class _Holders:
        available: bool = False

    async def fake_get_token_holders(self, contract):
        return _Holders()

    async def fake_get_address_info(self, address):
        @dataclass
        class _Info:
            creator_address: str | None = None
            is_contract: bool | None = None
            available: bool = False
        return _Info()

    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_token_holders", fake_get_token_holders)
    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_address_info", fake_get_address_info)

    async def fake_facts(contract, creator, *, lp_address=None, client=None, holders=None):
        return dw_mod.DevWalletFacts(creator=None, available=False, error="déployeur inconnu")

    monkeypatch.setattr(dw_mod, "gather_dev_wallet_facts", fake_facts)

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        return sm_mod.SmartMoneySignal(available=False)

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)


async def test_compute_returns_none_score_for_non_base_chain():
    result = await dcs.compute_dex_composite_score(
        "0xcontract", "ethereum", pair=_pair(), security=_security(),
    )
    assert result.score is None
    assert "non-Base" in result.reasons[0]


async def test_compute_aggregates_all_four_pillars_on_base():
    result = await dcs.compute_dex_composite_score(
        "0xcontract", "base", pair=_pair(), security=_security(),
    )
    assert result.score is not None
    assert result.score == pytest.approx(
        result.score_contract_risk + result.score_dev_behavior
        + result.score_smart_money + result.score_liquidity_depth
    )
    assert any("score composite DEX" in r for r in result.reasons)


async def test_compute_never_raises_when_security_is_none():
    result = await dcs.compute_dex_composite_score(
        "0xcontract", "base", pair=_pair(), security=None,
    )
    # pillar 1 unresolved, but the other 3 still contribute -> never a hard None
    assert result.score is not None
    assert result.score_contract_risk is None


async def test_compute_neutral_floor_is_exactly_35_when_nothing_confirmed_anywhere():
    """28/07 2nd pass, the explicit operator target: a candidate with ZERO
    positively-confirmed signal on ANY pillar must land exactly at 35.0/100
    (0.35 * 100, since every pillar's neutral share is 35% of its own
    weight) -- below risk_guard.DEX_SECURITY_WEAK_THRESHOLD (40)."""
    from aria_core import risk_guard

    result = await dcs.compute_dex_composite_score(
        "0xcontract", "base", pair=_pair(market_cap_usd=None), security=_security(),
    )
    assert result.score == pytest.approx(35.0)
    assert result.score < risk_guard.DEX_SECURITY_WEAK_THRESHOLD


async def test_compute_full_credit_when_everything_confirmed_good(monkeypatch):
    """Symmetric check: every pillar positively confirmed good/strong ->
    full 100/100, the ceiling still reachable."""
    from aria_core.services import smart_money as sm_mod
    from aria_core.services import blockscout as blockscout_mod
    import aria_core.dex_composite_score as dcs_mod

    async def fake_analyze(token_address, holders, *, client, lp_address=None, pair_created_at_ms=None, max_wallets=8):
        return sm_mod.SmartMoneySignal(available=True, quality_signal=100.0, smart_wallets=["0x1", "0x2"])

    monkeypatch.setattr(sm_mod, "analyze_smart_money", fake_analyze)

    async def fake_dev_behavior(contract, security, holders):
        return dcs_mod._WEIGHT_DEV_BEHAVIOR, "comportement déployeur aligné (test)"

    monkeypatch.setattr(dcs_mod, "_score_dev_behavior", fake_dev_behavior)

    async def fake_get_token_holders(self, contract):
        @dataclass
        class _Holders:
            available: bool = True
        return _Holders()

    monkeypatch.setattr(type(blockscout_mod.blockscout_client), "get_token_holders", fake_get_token_holders)

    # Distinct contract address (never reused by any other test in this file)
    # -- momentum_entry._cached_get_token_holders keys its TTL cache on
    # (chain, contract.lower()) at MODULE level, shared across the whole test
    # session; reusing "0xcontract" here would silently pick up a stale
    # holders result cached by an earlier test in this same file.
    result = await dcs_mod.compute_dex_composite_score(
        "0xfullcreditcase", "base",
        pair=_pair(liquidity_usd=90_000.0, market_cap_usd=100_000.0),
        security=_security(
            hidden_owner=False, can_take_back_ownership=False, slippage_modifiable=False,
            is_blacklisted=False, is_open_source=True, buy_tax=0.0, sell_tax=0.0, is_mintable=False,
        ),
    )
    assert result.score == pytest.approx(100.0)
