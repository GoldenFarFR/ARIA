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
from aria_core.services import smart_money as smart_money_module
from aria_core.services.smart_money import analyze_smart_money

TOKEN = "0x" + "t" * 40
LP = "0x" + "1" * 40
WALLET_A = "0x" + "a" * 40
WALLET_B = "0x" + "b" * 40
COUNTERPARTY = "0x" + "c" * 40

# 2026-01-01T00:00:00Z en millisecondes — sert de pair_created_at de reference
PAIR_CREATED_MS = 1767225600000


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """22/07 -- analyze_smart_money() lit désormais wallet_score_log (signal qualité-
    prioritaire, cf. latest_score_for_wallet) : sans cette isolation, DB_PATH resterait
    figé sur la valeur calculée au premier import du module dans la session pytest
    (même patron que test_weekly_training.py sur screened_pool/vc_predictions.DB_PATH) --
    jamais une fuite d'état entre tests."""
    monkeypatch.setattr(smart_money_module, "DB_PATH", str(tmp_path / "smart_money_test.db"))


async def _seed_wallet_score(wallet: str, composite_percentile: float) -> None:
    """Insère une fiche wallet_score_log minimale pour tester le signal qualité-
    prioritaire sans dépendre du pipeline lourd #157 (FIFO/PnL/percentile réel)."""
    import json

    await smart_money_module._log_wallet_score(
        wallet, json.dumps({"composite_percentile": composite_percentile})
    )


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
    # 22/07 -- ni WALLET_A ni WALLET_B n'a de composite_percentile connu (wallet_score_log
    # vide) -> fallback modeste (55) chacun. quality_signal = 55 + bonus(2 wallets: +3) = 58,
    # score_delta = round(58/100*15) = 9 (remplace l'ancien forfait fixe +8, cf. constantes
    # _CONVERGENCE_BONUS_PER_WALLET/_FALLBACK_QUALIFIED_SCORE/_MAX_SECURITY_SCORE_DELTA).
    assert signal.quality_signal == 58
    assert signal.score_delta == 9
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


def _addr(i: int) -> str:
    """Adresse hex valide (40 caractères) dérivée d'un entier — évite toute collision
    avec TOKEN/LP/WALLET_A/WALLET_B/COUNTERPARTY (lettres répétées ci-dessus)."""
    return f"0x{i:040x}"


def _convergent_transfers(wallet: str) -> TokenTransfersResult:
    """Même pattern que test_two_convergent_wallets_confirm_smart_money (cohérence
    temporelle + entrée précoce/contrôlée + sortie disciplinée) — is_smart_candidate
    vrai pour n'importe quel wallet utilisant ce pattern."""
    return TokenTransfersResult(
        transfers=[
            _transfer(from_addr=LP, to_addr=wallet, timestamp="2026-01-01T12:00:00Z", amount=50.0),
            _transfer(from_addr=LP, to_addr=wallet, timestamp="2026-01-02T12:00:00Z", amount=50.0),
            _transfer(from_addr=wallet, to_addr=COUNTERPARTY, timestamp="2026-01-10T12:00:00Z", amount=40.0),
        ],
        available=True,
    )


@pytest.mark.asyncio
async def test_high_composite_score_wallets_beat_many_low_score_wallets():
    """22/07 -- exemple chiffré validé explicitement avec l'opérateur : 2 wallets à
    gros composite_percentile (95) doivent produire un signal PLUS FORT que 10
    wallets à faible composite_percentile (45), jamais l'inverse -- la qualité
    prime sur la pure quantité (cf. constantes _CONVERGENCE_BONUS_*/_MAX_SECURITY_
    SCORE_DELTA en tête de smart_money.py)."""
    # Cas A : 2 wallets, composite_percentile élevé (95) connu.
    wallets_high = [_addr(100), _addr(101)]
    for w in wallets_high:
        await _seed_wallet_score(w, 95.0)
    fixtures_high = {w: _WalletFixture(info=_eoa(), transfers=_convergent_transfers(w)) for w in wallets_high}
    signal_high = await analyze_smart_money(
        TOKEN, _holders(*wallets_high), client=FakeClient(fixtures_high),
        lp_address=LP, pair_created_at_ms=PAIR_CREATED_MS,
    )

    # Cas B : 10 wallets, composite_percentile faible (45) connu chacun.
    wallets_low = [_addr(200 + i) for i in range(10)]
    for w in wallets_low:
        await _seed_wallet_score(w, 45.0)
    fixtures_low = {w: _WalletFixture(info=_eoa(), transfers=_convergent_transfers(w)) for w in wallets_low}
    signal_low = await analyze_smart_money(
        TOKEN, _holders(*wallets_low), client=FakeClient(fixtures_low),
        lp_address=LP, pair_created_at_ms=PAIR_CREATED_MS, max_wallets=10,
    )

    assert signal_high.quality_signal == 98.0  # top=95 + bonus(2 wallets: +3), plafonné à 100
    assert signal_low.quality_signal == 54.0  # top=45 + bonus plafonné (min(9,3)*3=9)
    assert signal_high.score_delta > signal_low.score_delta
    assert signal_high.score_delta == 15  # round(98/100*15)
    assert signal_low.score_delta == 8  # round(54/100*15)


@pytest.mark.asyncio
async def test_known_composite_score_overrides_fallback():
    """Un wallet déjà scoré ailleurs (composite_percentile connu) utilise CE score
    plutôt que le repli modeste (_FALLBACK_QUALIFIED_SCORE=55) -- la qualité déjà
    prouvée sur d'autres tokens prime sur le jugement limité à ce seul token."""
    await _seed_wallet_score(WALLET_A, 90.0)
    # WALLET_B n'a jamais été scoré ailleurs -> repli à 55.
    fixtures = {
        WALLET_A: _WalletFixture(info=_eoa(), transfers=_convergent_transfers(WALLET_A)),
        WALLET_B: _WalletFixture(info=_eoa(), transfers=_convergent_transfers(WALLET_B)),
    }
    signal = await analyze_smart_money(
        TOKEN, _holders(WALLET_A, WALLET_B), client=FakeClient(fixtures),
        lp_address=LP, pair_created_at_ms=PAIR_CREATED_MS,
    )

    # top_score = 90 (WALLET_A, connu) ; bonus = +3 (2 wallets qualifiés) -> 93
    assert signal.quality_signal == 93.0


@pytest.mark.asyncio
async def test_latest_score_for_wallet_none_when_never_scored():
    from aria_core.services.smart_money import latest_score_for_wallet

    assert await latest_score_for_wallet(WALLET_A) is None


@pytest.mark.asyncio
async def test_latest_score_for_wallet_returns_most_recent():
    await _seed_wallet_score(WALLET_A, 40.0)
    await _seed_wallet_score(WALLET_A, 70.0)  # scan plus récent -- doit primer

    from aria_core.services.smart_money import latest_score_for_wallet

    assert await latest_score_for_wallet(WALLET_A) == 70.0
