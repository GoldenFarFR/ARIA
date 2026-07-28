"""dex_composite_score.py -- additive DEX security/conviction score (Item #177,
28/07). Aucun appel réseau réel, tout est mocké."""
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
# Pillar 1 -- contract/dev residual risk (pure function)
# ---------------------------------------------------------------------------
def test_score_contract_risk_none_when_security_unavailable():
    score, reason = dcs._score_contract_risk(None)
    assert score is None
    assert "indisponible" in reason


def test_score_contract_risk_none_when_security_not_available():
    score, reason = dcs._score_contract_risk(_security(available=False))
    assert score is None


def test_score_contract_risk_full_when_nothing_confirmed_bad():
    score, reason = dcs._score_contract_risk(_security())
    assert score == dcs._WEIGHT_CONTRACT_RISK
    assert "rien de confirmé" in reason


def test_score_contract_risk_penalizes_hidden_owner():
    score, _ = dcs._score_contract_risk(_security(hidden_owner=True))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._HIDDEN_OWNER_PENALTY


def test_score_contract_risk_penalizes_can_take_back_ownership():
    score, _ = dcs._score_contract_risk(_security(can_take_back_ownership=True))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._CAN_TAKE_BACK_OWNERSHIP_PENALTY


def test_score_contract_risk_penalizes_slippage_modifiable():
    score, _ = dcs._score_contract_risk(_security(slippage_modifiable=True))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._SLIPPAGE_MODIFIABLE_PENALTY


def test_score_contract_risk_penalizes_is_blacklisted():
    score, _ = dcs._score_contract_risk(_security(is_blacklisted=True))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._IS_BLACKLISTED_PENALTY


def test_score_contract_risk_penalizes_confirmed_not_open_source():
    score, _ = dcs._score_contract_risk(_security(is_open_source=False))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._NOT_OPEN_SOURCE_PENALTY


def test_score_contract_risk_never_penalizes_unknown_open_source():
    """``is_open_source=None`` (unknown) must never be treated like ``False``
    (confirmed unverified) -- fail-open doctrine."""
    score, _ = dcs._score_contract_risk(_security(is_open_source=None))
    assert score == dcs._WEIGHT_CONTRACT_RISK


def test_score_contract_risk_tax_penalty_scales_with_combined_tax():
    score, reason = dcs._score_contract_risk(_security(buy_tax=0.10, sell_tax=0.10))
    expected_penalty = min(
        dcs._TAX_PENALTY_MAX, (0.20 / dcs._TAX_PENALTY_REFERENCE_PCT) * dcs._TAX_PENALTY_MAX
    )
    assert score == pytest.approx(dcs._WEIGHT_CONTRACT_RISK - expected_penalty)
    assert "taxe combinée" in reason


def test_score_contract_risk_tax_penalty_capped_at_max():
    score, _ = dcs._score_contract_risk(_security(buy_tax=0.50, sell_tax=0.50))
    assert score == dcs._WEIGHT_CONTRACT_RISK - dcs._TAX_PENALTY_MAX


def test_score_contract_risk_never_goes_negative():
    score, _ = dcs._score_contract_risk(
        _security(
            hidden_owner=True, can_take_back_ownership=True, slippage_modifiable=True,
            is_blacklisted=True, is_open_source=False, buy_tax=0.5, sell_tax=0.5,
        )
    )
    assert score == 0.0


def test_score_contract_risk_stacks_multiple_confirmed_penalties():
    score, reason = dcs._score_contract_risk(_security(hidden_owner=True, is_blacklisted=True))
    expected = dcs._WEIGHT_CONTRACT_RISK - dcs._HIDDEN_OWNER_PENALTY - dcs._IS_BLACKLISTED_PENALTY
    assert score == expected
    assert "owner caché" in reason
    assert "contrat peut blacklister" in reason


# ---------------------------------------------------------------------------
# Pillar 1b -- mint authority resolution
# ---------------------------------------------------------------------------
async def test_resolve_mint_penalty_zero_when_no_mint():
    penalty, reason = await dcs._resolve_mint_penalty("0xcontract", _security(is_mintable=False))
    assert penalty == 0.0
    assert "pas de fonction mint" in reason


async def test_resolve_mint_penalty_zero_when_security_unavailable():
    penalty, reason = await dcs._resolve_mint_penalty("0xcontract", _security(available=False))
    assert penalty == 0.0


async def test_resolve_mint_penalty_eoa_owner(monkeypatch):
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

    penalty, reason = await dcs._resolve_mint_penalty(
        "0xcontract", _security(is_mintable=True, owner_address="0xowner"),
    )
    assert penalty == dcs._MINT_EOA_PENALTY
    assert "wallet externe" in reason


async def test_resolve_mint_penalty_launchpad_neutralized(monkeypatch):
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

    penalty, reason = await dcs._resolve_mint_penalty("0xcontract", _security(is_mintable=True))
    assert penalty == 0.0
    assert "neutralisé" in reason


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


async def test_score_dev_behavior_unknown_scores_neutral_half(monkeypatch):
    from aria_core.skills import dev_wallet as dw_mod

    async def fake_facts(contract, creator, *, lp_address=None, client=None, holders=None):
        return dw_mod.DevWalletFacts(creator=None, available=False, error="déployeur inconnu")

    monkeypatch.setattr(dw_mod, "gather_dev_wallet_facts", fake_facts)
    monkeypatch.setattr(
        dw_mod, "judge_dev_wallet",
        lambda facts, **kw: dw_mod.DevWalletVerdict(signal="unknown", points=[facts.error]),
    )

    score, _ = await dcs._score_dev_behavior("0xcontract", _security(available=False), None)
    assert score == dcs._WEIGHT_DEV_BEHAVIOR / 2.0


# ---------------------------------------------------------------------------
# Pillar 3 -- smart money (generalized)
# ---------------------------------------------------------------------------
async def test_score_smart_money_neutral_when_holders_unavailable():
    @dataclass
    class _Holders:
        available: bool = False

    score, reason = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == dcs._WEIGHT_SMART_MONEY / 2.0
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

    score, _ = await dcs._score_smart_money("0xcontract", _Holders(), _pair())
    assert score == dcs._WEIGHT_SMART_MONEY / 2.0


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
    assert score == dcs._WEIGHT_LIQUIDITY_DEPTH / 2.0
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
