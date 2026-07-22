"""Signal 'sortie de liquidité déguisée' : wallets insiders hors 'creator'."""
from __future__ import annotations

import pytest

from aria_core.skills.insider_wallets import (
    InsiderWalletFacts,
    gather_insider_wallet_facts,
    judge_insider_wallets,
)

DEV = "0x" + "d" * 40
LP = "0x" + "e" * 40
TOKEN = "0x" + "a" * 40
INSIDER_1 = "0x" + "1" * 40
INSIDER_2 = "0x" + "2" * 40
INSIDER_DUST = "0x" + "3" * 40
ZERO = "0x0000000000000000000000000000000000000000"
PAIR_CREATED_AT_MS = 1782547200000  # 2026-06-23 08:00:00 UTC (env.)


# ── Jugement pur ────────────────────────────────────────────────────────────


def test_unavailable_is_unknown():
    v = judge_insider_wallets(InsiderWalletFacts(available=False, error="pas de données"))
    assert v.signal == "unknown"


def test_no_significant_recipient_is_neutral():
    v = judge_insider_wallets(InsiderWalletFacts(examined=0, available=True))
    assert v.signal == "neutral"


def test_examined_none_flagged_is_neutral():
    v = judge_insider_wallets(InsiderWalletFacts(examined=3, flagged=[], available=True))
    assert v.signal == "neutral"
    assert any("aucun n'a tout revendu" in p for p in v.points)


def test_flagged_wallet_is_concern():
    v = judge_insider_wallets(InsiderWalletFacts(examined=3, flagged=[INSIDER_1], available=True))
    assert v.signal == "concern"
    assert any("1/3" in p for p in v.points)


# ── Récolte on-chain (Dune + Blockscout factices) ───────────────────────────


class _Holder:
    def __init__(self, address, percentage):
        self.address, self.percentage = address, percentage


class _HoldersResult:
    def __init__(self, holders, available=True):
        self.holders, self.available = holders, available


class _FakeBlockscoutClient:
    def __init__(self, holders_result):
        self._holders_result = holders_result

    async def get_token_holders(self, contract):
        return self._holders_result


class _Recipient:
    def __init__(self, address, total_received_raw, first_received_at=None):
        self.address = address
        self.total_received_raw = total_received_raw
        self.first_received_at = first_received_at


class _InsiderRecipientsResult:
    def __init__(self, recipients, available=True, error=None):
        self.recipients, self.available, self.error = recipients, available, error


class _FakeDuneModule:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def get_insider_recipients(self, contract, deployer, *, window_start, window_end, limit=15):
        self.calls.append((contract, deployer, window_start, window_end, limit))
        return self._result


@pytest.mark.asyncio
async def test_gather_no_creator_is_unavailable():
    facts = await gather_insider_wallet_facts(TOKEN, None, pair_created_at_ms=PAIR_CREATED_AT_MS)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_no_pair_created_at_is_unavailable():
    facts = await gather_insider_wallet_facts(TOKEN, DEV, pair_created_at_ms=None)
    assert facts.available is False


@pytest.mark.asyncio
async def test_gather_dune_unavailable_propagates():
    dune_module = _FakeDuneModule(_InsiderRecipientsResult([], available=False, error="clé absente"))
    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, dune_module=dune_module,
    )
    assert facts.available is False
    assert facts.error == "clé absente"


@pytest.mark.asyncio
async def test_gather_no_recipients_is_examined_zero():
    dune_module = _FakeDuneModule(_InsiderRecipientsResult([]))
    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, dune_module=dune_module,
    )
    assert facts.available is True
    assert facts.examined == 0


@pytest.mark.asyncio
async def test_gather_holders_unavailable_is_fail_closed_unavailable():
    """Sans les holders actuels, impossible de savoir si un insider a tout revendu --
    ne JAMAIS déduire 'a tout vendu' d'une absence de donnée (doctrine fail-closed)."""
    dune_module = _FakeDuneModule(_InsiderRecipientsResult([_Recipient(INSIDER_1, 1.0e21)]))
    client = _FakeBlockscoutClient(_HoldersResult([], available=False))
    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, dune_module=dune_module, client=client,
    )
    assert facts.available is False
    assert facts.flagged == []


@pytest.mark.asyncio
async def test_gather_flags_insider_who_sold_everything():
    recipients = [
        _Recipient(DEV, 1.0e24),          # le déployeur lui-même -- exclu (dev_wallet.py le couvre)
        _Recipient(INSIDER_1, 1.0e22),    # 1% du top -- allocation significative, revendue
        _Recipient(INSIDER_DUST, 1.0e19), # < 1% du top (dev) -- micro-transfert, ignoré
    ]
    dune_module = _FakeDuneModule(_InsiderRecipientsResult(recipients))
    holders = _HoldersResult([_Holder(DEV, 10.0), _Holder(INSIDER_1, 0.0)])
    client = _FakeBlockscoutClient(holders)

    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, lp_address=LP,
        dune_module=dune_module, client=client,
    )

    assert facts.available is True
    assert facts.examined == 1  # seul INSIDER_1 dépasse le seuil de 1% du top recipient
    assert facts.flagged == [INSIDER_1]


@pytest.mark.asyncio
async def test_gather_insider_still_holding_not_flagged():
    recipients = [_Recipient(DEV, 1.0e24), _Recipient(INSIDER_1, 5.0e23)]
    dune_module = _FakeDuneModule(_InsiderRecipientsResult(recipients))
    holders = _HoldersResult([_Holder(DEV, 10.0), _Holder(INSIDER_1, 8.0)])
    client = _FakeBlockscoutClient(holders)

    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, dune_module=dune_module, client=client,
    )

    assert facts.examined == 1
    assert facts.flagged == []


@pytest.mark.asyncio
async def test_gather_excludes_lp_and_zero_address():
    recipients = [
        _Recipient(DEV, 1.0e24),
        _Recipient(LP, 5.0e23),
        _Recipient(ZERO, 5.0e23),
        _Recipient(INSIDER_2, 5.0e23),
    ]
    dune_module = _FakeDuneModule(_InsiderRecipientsResult(recipients))
    holders = _HoldersResult([_Holder(DEV, 10.0)])
    client = _FakeBlockscoutClient(holders)

    facts = await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, lp_address=LP,
        dune_module=dune_module, client=client,
    )

    assert facts.examined == 1  # LP + ZERO exclus, seul INSIDER_2 compte
    assert facts.flagged == [INSIDER_2]


@pytest.mark.asyncio
async def test_gather_passes_window_bounds_derived_from_pair_created_at():
    dune_module = _FakeDuneModule(_InsiderRecipientsResult([]))
    await gather_insider_wallet_facts(
        TOKEN, DEV, pair_created_at_ms=PAIR_CREATED_AT_MS, dune_module=dune_module,
    )
    assert len(dune_module.calls) == 1
    contract, deployer, window_start, window_end, limit = dune_module.calls[0]
    assert contract == TOKEN
    assert deployer == DEV
    assert window_start < window_end
