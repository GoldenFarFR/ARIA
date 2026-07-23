"""Jugement du wallet du dev : builder engagé vs farmer, au cas par cas."""
from __future__ import annotations

from aria_core.skills.dev_wallet import DevWalletFacts, judge_dev_wallet


def _facts(**kw) -> DevWalletFacts:
    base = dict(creator="0x" + "d" * 40, available=True)
    base.update(kw)
    return DevWalletFacts(**base)


def test_unavailable_is_unknown():
    v = judge_dev_wallet(DevWalletFacts(creator=None, available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_bought_and_holding_is_aligned():
    v = judge_dev_wallet(_facts(holds_pct=8.0, acquired="bought", sold_pct_of_received=0.0))
    assert v.signal == "aligned"
    assert any("ACHETÉ" in p for p in v.points)


def test_heavy_early_sell_is_concern():
    v = judge_dev_wallet(_facts(holds_pct=5.0, acquired="allocation", sold_events=1, sold_pct_of_received=80.0))
    assert v.signal == "concern"
    assert any("extraction" in p for p in v.points)


def test_very_high_concentration_is_concern():
    v = judge_dev_wallet(_facts(holds_pct=55.0, acquired="allocation"))
    assert v.signal == "concern"
    assert any("dump" in p for p in v.points)


def test_virtuals_team_norm_is_not_concern():
    # 18% détenu MAIS dans la norme Virtuals (15-20) -> aligné, pas un concern
    v = judge_dev_wallet(_facts(holds_pct=18.0, acquired="allocation"), launchpad_team_norm=(15.0, 20.0))
    assert v.signal in ("aligned", "neutral")
    assert any("norme du launchpad" in p for p in v.points)


def test_solo_dev_zero_holding_not_penalized():
    # dev solo qui ne détient rien : neutre (moins de pression), pas un concern
    v = judge_dev_wallet(_facts(holds_pct=0.0, acquired="allocation"), team_is_large=False)
    assert v.signal == "neutral"


def test_large_team_zero_holding_is_concern():
    # équipe organisée qui n'engage rien = incohérent
    v = judge_dev_wallet(_facts(holds_pct=0.0), team_is_large=True)
    assert v.signal == "concern"
    assert any("incohérent" in p for p in v.points)


def test_staggered_sells_read_as_possible_funding():
    v = judge_dev_wallet(_facts(holds_pct=6.0, acquired="bought", sold_events=4, sold_pct_of_received=30.0))
    assert any("financement" in p for p in v.points)
    # a acheté + garde une position -> pas un concern
    assert v.signal in ("aligned", "neutral")


def test_all_in_no_sell_is_aligned():
    v = judge_dev_wallet(_facts(holds_pct=10.0, acquired="bought", sold_events=0, sold_pct_of_received=0.0))
    assert v.signal == "aligned"
    assert any("conviction" in p for p in v.points)


# ── Récolte on-chain (client factice) ───────────────────────────────────────

import pytest
from aria_core.skills.dev_wallet import gather_dev_wallet_facts

DEV = "0x" + "d" * 40
LP = "0x" + "e" * 40
TOKEN = "0x" + "a" * 40
ZERO = "0x0000000000000000000000000000000000000000"


class _Holder:
    def __init__(self, address, percentage):
        self.address, self.percentage = address, percentage


class _Transfer:
    def __init__(self, frm, to, amount, token=TOKEN):
        self.from_address, self.to_address, self.amount, self.token_address = frm, to, amount, token


class _FakeClient:
    def __init__(self, holders, transfers):
        self._holders, self._transfers = holders, transfers

    async def get_token_holders(self, contract):
        class R:
            available = True
            holders = self._holders
        r = R(); r.holders = self._holders; return r

    async def get_token_transfers(self, address, limit=100):
        class R:
            transfers = self._transfers
        r = R(); r.transfers = self._transfers; return r


@pytest.mark.asyncio
async def test_gather_no_creator_is_unavailable():
    facts = await gather_dev_wallet_facts(TOKEN, None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_classifies_allocation_and_sells():
    holders = [_Holder(DEV, 12.0)]
    transfers = [
        _Transfer(ZERO, DEV, 1000),   # allocation
        _Transfer(DEV, LP, 300),      # vente 1
        _Transfer(DEV, LP, 200),      # vente 2
    ]
    facts = await gather_dev_wallet_facts(TOKEN, DEV, lp_address=LP, client=_FakeClient(holders, transfers))
    assert facts.available is True
    assert facts.holds_pct == 12.0
    assert facts.acquired == "allocation"
    assert facts.sold_events == 2
    assert facts.sold_pct_of_received == 50.0  # 500 vendus sur 1000 reçus


@pytest.mark.asyncio
async def test_gather_detects_buy_from_pool():
    holders = [_Holder(DEV, 5.0)]
    transfers = [
        _Transfer(ZERO, DEV, 500),    # allocation
        _Transfer(LP, DEV, 500),      # achat depuis le pool
    ]
    facts = await gather_dev_wallet_facts(TOKEN, DEV, lp_address=LP, client=_FakeClient(holders, transfers))
    assert facts.acquired == "mixed"  # allocation + achat


@pytest.mark.asyncio
async def test_gather_dev_absent_from_holders_means_zero():
    facts = await gather_dev_wallet_facts(TOKEN, DEV, lp_address=LP, client=_FakeClient([], []))
    assert facts.holds_pct == 0.0


# ── Calibration en live (23/07, tâche #26) : Uniswap V4 PoolManager singleton ──


UNISWAP_V4_POOL_MANAGER = "0x498581ff718922c3f8e6a244956af099b2652b2b"


@pytest.mark.asyncio
async def test_gather_detects_buy_via_v4_pool_manager_without_lp_address():
    """Vérifié en direct (contrat CNX réel) : sur un pool Uniswap V4, TOUS les
    swaps transitent par le PoolManager SINGLETON, jamais par une adresse de
    pool dédiée -- doit être détecté même sans lp_address fourni."""
    holders = [_Holder(DEV, 5.0)]
    transfers = [_Transfer(UNISWAP_V4_POOL_MANAGER, DEV, 500)]
    facts = await gather_dev_wallet_facts(TOKEN, DEV, client=_FakeClient(holders, transfers))
    assert facts.acquired == "bought"


@pytest.mark.asyncio
async def test_gather_detects_sell_via_v4_pool_manager():
    holders = [_Holder(DEV, 5.0)]
    transfers = [
        _Transfer(ZERO, DEV, 1000),
        _Transfer(DEV, UNISWAP_V4_POOL_MANAGER, 400),
    ]
    facts = await gather_dev_wallet_facts(TOKEN, DEV, client=_FakeClient(holders, transfers))
    assert facts.sold_events == 1
    assert facts.sold_pct_of_received == 40.0


@pytest.mark.asyncio
async def test_gather_v4_pool_manager_combined_with_real_lp_address():
    """lp_address (pool V2/V3 classique) ET le PoolManager V4 doivent tous
    les deux compter -- jamais l'un au détriment de l'autre."""
    holders = [_Holder(DEV, 5.0)]
    transfers = [
        _Transfer(LP, DEV, 300),                    # achat via pool V2/V3 classique
        _Transfer(UNISWAP_V4_POOL_MANAGER, DEV, 200),  # achat via PoolManager V4
    ]
    facts = await gather_dev_wallet_facts(TOKEN, DEV, lp_address=LP, client=_FakeClient(holders, transfers))
    assert facts.acquired == "bought"  # 500 reçus, 500 achetés (300+200) -- aucune allocation
