"""Wallet-tracker smart-money — lecture seule, aucun appel réseau réel (tout mocké).

Vérifie les 4 critères croisés (cohérence temporelle, entrée précoce +
contrôlée, sortie disciplinée, concentration multi-wallets), l'élimination
des faux signaux (wash-trading, wallets contrat), et que ce module ne
produit jamais qu'un signal additif de confirmation/contexte.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aria_core.services.blockscout import (
    AddressInfo,
    TokenHolder,
    TokenHoldersResult,
    TokenTransfer,
    TokenTransfersResult,
)
from aria_core.services.smart_money import analyze_smart_money

TOKEN = "0x" + "t" * 40
LP = "0x" + "1" * 40
WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40
COUNTERPARTY = "0x" + "c" * 40

# 2026-01-01T00:00:00Z en millisecondes — sert de pair_created_at de reference
PAIR_CREATED_MS = 1767225600000


def _transfer(*, from_addr: str, to_addr: str, timestamp: str, amount: float = 100.0) -> TokenTransfer:
    return TokenTransfer(
        tx_hash="0x1",
        from_address=from_addr,
        to_address=to_addr,
        token_address=TOKEN,
        token_symbol="TOK",
        token_name="Token",
        amount=amount,
        timestamp=timestamp,
    )


def _holders(*addresses: str) -> TokenHoldersResult:
    return TokenHoldersResult(
        holders=[TokenHolder(address=a, balance=100.0, percentage=10.0) for a in addresses],
        total_supply=1000.0,
        available=True,
        error=None,
    )


@dataclass
class _WalletFixture:
    info: AddressInfo
    transfers: TokenTransfersResult


class FakeClient:
    """Simule BlockscoutClient.get_address_info / get_token_transfers par wallet."""

    def __init__(self, fixtures: dict[str, _WalletFixture]):
        self._fixtures = fixtures

    async def get_address_info(self, address: str) -> AddressInfo:
        return self._fixtures[address].info

    async def get_token_transfers(self, address: str, limit: int = 50) -> TokenTransfersResult:
        return self._fixtures[address].transfers


def _eoa(is_contract: bool = False) -> AddressInfo:
    return AddressInfo(address="", is_contract=is_contract, available=True, error=None)


@pytest.mark.asyncio
async def test_no_holders_returns_empty_signal():
    holders = _holders()
    signal = await analyze_smart_money(TOKEN, holders, client=FakeClient({}))

    assert signal.available is True
    assert signal.wallets_analyzed == 0
    assert signal.smart_wallets == []
    assert signal.score_delta == 0


@pytest.mark.asyncio
async def test_holders_unavailable_propagates_error():
    holders = TokenHoldersResult(available=False, error="donnée on-chain indisponible (timeout Blockscout)")
    signal = await analyze_smart_money(TOKEN, holders, client=FakeClient({}))

    assert signal.available is False
    assert "indisponible" in signal.error


@pytest.mark.asyncio
async def test_lp_address_excluded_from_analysis():
    holders = _holders(LP)
    signal = await analyze_smart_money(TOKEN, holders, client=FakeClient({}), lp_address=LP)

    assert signal.wallets_analyzed == 0


@pytest.mark.asyncio
async def test_two_convergent_wallets_confirm_smart_money():
    fixtures = {}
    for wallet in (WALLET_A, WALLET_B):
        transfers = TokenTransfersResult(
            transfers=[
                _transfer(from_addr=LP, to_addr=wallet, timestamp="2026-01-01T12:00:00Z", amount=50.0),
                _transfer(from_addr=LP, to_addr=wallet, timestamp="2026-01-02T12:00:00Z", amount=50.0),
                _transfer(from_addr=wallet, to_addr=COUNTERPARTY, timestamp="2026-01-10T12:00:00Z", amount=40.0),
            ],
            available=True,
        )
        fixtures[wallet] = _WalletFixture(info=_eoa(), transfers=transfers)

    holders = _holders(WALLET_A, WALLET_B)
    signal = await analyze_smart_money(
        TOKEN, holders, client=FakeClient(fixtures), lp_address=LP, pair_created_at_ms=PAIR_CREATED_MS
    )

    assert signal.wallets_analyzed == 2
    assert set(signal.smart_wallets) == {WALLET_A, WALLET_B}
    assert signal.score_delta == 8
    assert any("confirmation contextuelle" in f for f in signal.flags)
    assert any("jamais un déclencheur" in f for f in signal.flags)


@pytest.mark.asyncio
async def test_single_convergent_wallet_not_enough_concentration():
    transfers = TokenTransfersResult(
        transfers=[
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-01-01T12:00:00Z", amount=50.0),
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-01-02T12:00:00Z", amount=50.0),
            _transfer(from_addr=WALLET_A, to_addr=COUNTERPARTY, timestamp="2026-01-10T12:00:00Z", amount=40.0),
        ],
        available=True,
    )
    holders = _holders(WALLET_A)
    signal = await analyze_smart_money(
        TOKEN,
        holders,
        client=FakeClient({WALLET_A: _WalletFixture(info=_eoa(), transfers=transfers)}),
        lp_address=LP,
        pair_created_at_ms=PAIR_CREATED_MS,
    )

    assert signal.smart_wallets == [WALLET_A]
    assert signal.score_delta == 0
    assert any("concentration insuffisante" in f for f in signal.flags)


@pytest.mark.asyncio
async def test_contract_wallet_excluded_even_if_pattern_matches():
    transfers = TokenTransfersResult(
        transfers=[
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-01-01T12:00:00Z", amount=50.0),
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-01-02T12:00:00Z", amount=50.0),
            _transfer(from_addr=WALLET_A, to_addr=COUNTERPARTY, timestamp="2026-01-10T12:00:00Z", amount=40.0),
        ],
        available=True,
    )
    holders = _holders(WALLET_A)
    signal = await analyze_smart_money(
        TOKEN,
        holders,
        client=FakeClient({WALLET_A: _WalletFixture(info=_eoa(is_contract=True), transfers=transfers)}),
        lp_address=LP,
        pair_created_at_ms=PAIR_CREATED_MS,
    )

    assert signal.smart_wallets == []


@pytest.mark.asyncio
async def test_wash_trading_pattern_excluded():
    """Aller-retour quasi exclusif avec une seule contrepartie -> exclu, pas 'smart'."""
    transfers = TokenTransfersResult(
        transfers=[
            _transfer(from_addr=COUNTERPARTY, to_addr=WALLET_A, timestamp="2026-01-01T12:00:00Z", amount=50.0),
            _transfer(from_addr=WALLET_A, to_addr=COUNTERPARTY, timestamp="2026-01-01T13:00:00Z", amount=50.0),
            _transfer(from_addr=COUNTERPARTY, to_addr=WALLET_A, timestamp="2026-01-02T12:00:00Z", amount=50.0),
            _transfer(from_addr=WALLET_A, to_addr=COUNTERPARTY, timestamp="2026-01-02T13:00:00Z", amount=50.0),
        ],
        available=True,
    )
    holders = _holders(WALLET_A)
    signal = await analyze_smart_money(
        TOKEN,
        holders,
        client=FakeClient({WALLET_A: _WalletFixture(info=_eoa(), transfers=transfers)}),
        lp_address=LP,
        pair_created_at_ms=PAIR_CREATED_MS,
    )

    assert signal.smart_wallets == []


@pytest.mark.asyncio
async def test_transfers_unavailable_for_wallet_counted_not_crashing():
    holders = _holders(WALLET_A)
    signal = await analyze_smart_money(
        TOKEN,
        holders,
        client=FakeClient(
            {
                WALLET_A: _WalletFixture(
                    info=_eoa(),
                    transfers=TokenTransfersResult(available=False, error="donnée on-chain indisponible (timeout Blockscout)"),
                )
            }
        ),
    )

    assert signal.available is True
    assert signal.wallets_analyzed == 1
    assert signal.smart_wallets == []
    assert any("non analysable" in f for f in signal.flags)


@pytest.mark.asyncio
async def test_late_entry_not_considered_early():
    """Achat bien apres la creation de la paire, aucune sortie -> un seul critere (coherence),
    en dessous du seuil de 2 -> pas de smart-money confirme."""
    transfers = TokenTransfersResult(
        transfers=[
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-03-01T12:00:00Z", amount=50.0),
            _transfer(from_addr=LP, to_addr=WALLET_A, timestamp="2026-03-02T12:00:00Z", amount=50.0),
        ],
        available=True,
    )
    holders = _holders(WALLET_A)
    signal = await analyze_smart_money(
        TOKEN,
        holders,
        client=FakeClient({WALLET_A: _WalletFixture(info=_eoa(), transfers=transfers)}),
        lp_address=LP,
        pair_created_at_ms=PAIR_CREATED_MS,
    )

    assert signal.smart_wallets == []
