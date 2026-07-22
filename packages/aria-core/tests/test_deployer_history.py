"""Réputation du déployeur : a-t-il déjà créé un contrat confirmé scam par ARIA ?"""
from __future__ import annotations

import pytest

from aria_core.services.deployer_history import (
    DeployerHistoryFacts,
    gather_deployer_history_facts,
    judge_deployer_history,
)

DEV = "0x" + "d" * 40
TOKEN = "0x" + "a" * 40
PRIOR_1 = "0x" + "1" * 40
PRIOR_2 = "0x" + "2" * 40


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_deployer_history(DeployerHistoryFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_no_prior_contracts_is_neutral():
    v = judge_deployer_history(DeployerHistoryFacts(prior_contracts_found=0, available=True))
    assert v.signal == "neutral"


def test_prior_contracts_but_no_known_rug_is_neutral():
    v = judge_deployer_history(DeployerHistoryFacts(prior_contracts_found=2, known_rugs=[], available=True))
    assert v.signal == "neutral"
    assert any("aucun déjà confirmé scam" in p for p in v.points)


def test_truncated_neutral_mentions_it_honestly():
    v = judge_deployer_history(
        DeployerHistoryFacts(prior_contracts_found=1, known_rugs=[], truncated=True, available=True)
    )
    assert any("borné" in p for p in v.points)


def test_known_rug_is_concern():
    v = judge_deployer_history(
        DeployerHistoryFacts(prior_contracts_found=2, known_rugs=[PRIOR_1], available=True)
    )
    assert v.signal == "concern"
    assert any("récidiviste" in p for p in v.points)


# ── Récolte on-chain (Blockscout + blacklist factices) ──────────────────────


class _Tx:
    def __init__(self, created_contract=None):
        self.created_contract = created_contract


class _TxResult:
    def __init__(self, transactions, available=True, error=None, truncated=False):
        self.transactions, self.available, self.error, self.truncated = (
            transactions, available, error, truncated,
        )


class _FakeClient:
    def __init__(self, result):
        self._result = result

    async def get_transactions_bounded(self, address, *, max_pages=3):
        return self._result


class _FakeBlacklist:
    def __init__(self, blacklisted: set[str]):
        self._blacklisted = blacklisted
        self.calls = []

    async def is_blacklisted(self, contract, chain):
        self.calls.append((contract, chain))
        return contract.lower() in self._blacklisted


@pytest.mark.asyncio
async def test_gather_no_creator_is_unavailable():
    facts = await gather_deployer_history_facts(None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_blockscout_unavailable_propagates():
    client = _FakeClient(_TxResult([], available=False, error="panne"))
    facts = await gather_deployer_history_facts(DEV, client=client, blacklist_module=_FakeBlacklist(set()))
    assert facts.available is False
    assert facts.error == "panne"


@pytest.mark.asyncio
async def test_gather_no_prior_contracts():
    client = _FakeClient(_TxResult([_Tx(), _Tx(created_contract=None)]))
    facts = await gather_deployer_history_facts(DEV, client=client, blacklist_module=_FakeBlacklist(set()))
    assert facts.available is True
    assert facts.prior_contracts_found == 0


@pytest.mark.asyncio
async def test_gather_dedupes_prior_contracts():
    client = _FakeClient(_TxResult([_Tx(created_contract=PRIOR_1), _Tx(created_contract=PRIOR_1)]))
    facts = await gather_deployer_history_facts(DEV, client=client, blacklist_module=_FakeBlacklist(set()))
    assert facts.prior_contracts_found == 1


@pytest.mark.asyncio
async def test_gather_excludes_current_contract():
    client = _FakeClient(_TxResult([_Tx(created_contract=TOKEN), _Tx(created_contract=PRIOR_1)]))
    facts = await gather_deployer_history_facts(
        DEV, exclude_contract=TOKEN, client=client, blacklist_module=_FakeBlacklist(set()),
    )
    assert facts.prior_contracts_found == 1


@pytest.mark.asyncio
async def test_gather_flags_known_rug():
    client = _FakeClient(_TxResult([_Tx(created_contract=PRIOR_1), _Tx(created_contract=PRIOR_2)]))
    blacklist = _FakeBlacklist({PRIOR_1.lower()})
    facts = await gather_deployer_history_facts(DEV, chain="base", client=client, blacklist_module=blacklist)
    assert facts.known_rugs == [PRIOR_1]
    assert facts.prior_contracts_found == 2
    assert all(chain == "base" for _, chain in blacklist.calls)


@pytest.mark.asyncio
async def test_gather_propagates_truncated_flag():
    client = _FakeClient(_TxResult([], truncated=True))
    facts = await gather_deployer_history_facts(DEV, client=client, blacklist_module=_FakeBlacklist(set()))
    assert facts.truncated is True


@pytest.mark.asyncio
async def test_gather_client_exception_is_unavailable_not_raised():
    class _RaisingClient:
        async def get_transactions_bounded(self, address, *, max_pages=3):
            raise RuntimeError("panne réseau")

    facts = await gather_deployer_history_facts(
        DEV, client=_RaisingClient(), blacklist_module=_FakeBlacklist(set()),
    )
    assert facts.available is False
