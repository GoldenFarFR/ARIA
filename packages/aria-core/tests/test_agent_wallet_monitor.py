"""Surveillance read-only du wallet agent (16/07, demande opérateur : détection
automatique des dépôts/retraits + registre complet) -- réutilise Blockscout
(déjà construit), aucun appel réseau réel ici, seulement des fakes injectés."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_cdp_adapter as adapter
from aria_core import agent_wallet_monitor as monitor
from aria_core.services.blockscout import (
    AddressInfo,
    Transaction,
    TransactionsResult,
    TokenTransfer,
    TokenTransfersResult,
)

WALLET = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"
USDC_ADDR = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # vraie adresse Base -- cf. agent_wallet_cdp_adapter.USDC_BASE_ADDRESS


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DB_PATH", str(tmp_path / "wallet_monitor_test.db"))
    yield


class FakeBlockscoutClient:
    def __init__(self, *, token_transfers=None, transactions=None):
        self._token_transfers = token_transfers or TokenTransfersResult(transfers=[], available=True)
        self._transactions = transactions or TransactionsResult(transactions=[], available=True)

    async def get_token_transfers(self, address, limit=50, *, max_pages=1, token_type=None):
        return self._token_transfers

    async def get_transactions(self, address, limit=50):
        return self._transactions


def _patch_client(monkeypatch, client: FakeBlockscoutClient):
    monkeypatch.setattr(monitor, "get_blockscout_client", lambda chain: client)


@pytest.mark.asyncio
async def test_no_movement_when_blockscout_has_nothing(monkeypatch):
    _patch_client(monkeypatch, FakeBlockscoutClient())
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert result == []


@pytest.mark.asyncio
async def test_incoming_usdc_transfer_classified_external_deposit(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xdeposit1", from_address="0xoperator", to_address=WALLET,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=1.0, timestamp="2026-07-16T19:00:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(result) == 1
    m = result[0]
    assert m.classification == "external_deposit"
    assert m.direction == "in"
    assert m.amount == 1.0
    assert m.asset == "USDC"
    assert m.counterparty == "0xoperator"


@pytest.mark.asyncio
async def test_outgoing_usdc_transfer_classified_unexpected_by_default(monkeypatch):
    """Le cas le plus critique : une sortie non journalisée par agent_wallet_log
    doit être signalée comme suspecte, jamais silencieuse."""
    transfer = TokenTransfer(
        tx_hash="0xoutflow1", from_address=WALLET, to_address="0xunknown",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=5.0, timestamp="2026-07-16T19:05:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(result) == 1
    assert result[0].classification == "unexpected_outflow"
    assert result[0].direction == "out"


@pytest.mark.asyncio
async def test_outgoing_transfer_classified_known_when_tx_hash_matches(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xariainitiated", from_address=WALLET, to_address="0x33783cCb570Cb279C25F836806B5c4C3C8309777",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=5.0, timestamp="2026-07-16T19:10:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(
        wallet_address=WALLET, known_tx_hashes={"0xariainitiated"},
    )
    assert len(result) == 1
    assert result[0].classification == "known"


# ── corrélation x402 (17/07) -- bug réel : le tout premier paiement x402 réel
# (Cybercentry, 0,02$) a déclenché une fausse alerte "SORTIE NON INITIÉE PAR
# ARIA" car x402_cdp_signer.py ne journalise jamais dans agent_wallet_log ────


@pytest.mark.asyncio
async def test_outgoing_transfer_classified_known_x402_when_correlated(monkeypatch):
    """Le cas réel vécu : même destinataire, même montant, dans la fenêtre de
    temps -- doit être reconnu comme un paiement x402 d'ARIA, pas une sortie
    suspecte, même sans tx_hash correspondant dans agent_wallet_log."""
    transfer = TokenTransfer(
        tx_hash="0xcybercentrypayment", from_address=WALLET,
        to_address="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=0.02, timestamp="2026-07-17T16:43:55+00:00",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    known_x402_spends = [{
        "pay_to": "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        "amount_usd": 0.02, "status": "ok",
        "created_at": "2026-07-17T16:43:52.325464+00:00",
    }]

    result = await monitor.check_wallet_activity(
        wallet_address=WALLET, known_x402_spends=known_x402_spends,
    )

    assert len(result) == 1
    assert result[0].classification == "known_x402"


@pytest.mark.asyncio
async def test_outgoing_transfer_not_correlated_when_amount_differs(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xoutflow2", from_address=WALLET,
        to_address="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=50.0, timestamp="2026-07-17T16:43:55+00:00",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    known_x402_spends = [{
        "pay_to": "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        "amount_usd": 0.02, "status": "ok",
        "created_at": "2026-07-17T16:43:52.325464+00:00",
    }]

    result = await monitor.check_wallet_activity(
        wallet_address=WALLET, known_x402_spends=known_x402_spends,
    )

    assert result[0].classification == "unexpected_outflow"


@pytest.mark.asyncio
async def test_outgoing_transfer_not_correlated_when_outside_time_window(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xoutflow3", from_address=WALLET,
        to_address="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=0.02, timestamp="2026-07-17T20:00:00+00:00",  # >30 min plus tard
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    known_x402_spends = [{
        "pay_to": "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        "amount_usd": 0.02, "status": "ok",
        "created_at": "2026-07-17T16:43:52.325464+00:00",
    }]

    result = await monitor.check_wallet_activity(
        wallet_address=WALLET, known_x402_spends=known_x402_spends,
    )

    assert result[0].classification == "unexpected_outflow"


def test_matches_known_x402_false_on_unparseable_timestamp():
    """Doute -> jamais de correspondance (fail-closed vers l'alerte, pas vers le
    silence) -- même doctrine que le reste du module.

    22/07 -- `_matches_known_x402` renvoie désormais le dict du spend matché (ou
    `None`) plutôt qu'un bool (cf. enrichissement de l'alerte known_x402) : assertion
    adaptée au nouveau contrat, logique de matching inchangée."""
    assert monitor._matches_known_x402(
        counterparty="0xabc", amount=0.02, timestamp="pas une date",
        known_x402_spends=[{"pay_to": "0xabc", "amount_usd": 0.02, "created_at": "2026-07-17T16:43:52Z"}],
    ) is None


def test_format_movement_alert_known_x402_uses_dedicated_label():
    from aria_core.agent_wallet_monitor import WalletMovement, format_movement_alert

    msg = format_movement_alert(WalletMovement(
        tx_hash="0xabc", direction="out", asset="USDC", amount=0.02,
        counterparty="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6", classification="known_x402",
    ))
    assert "Paiement x402 initié par ARIA (attendu)" in msg


# ── enrichissement token/service de l'alerte known_x402 (22/07) ────────────────


@pytest.mark.asyncio
async def test_outgoing_transfer_known_x402_enriched_with_matched_spend_contract(monkeypatch):
    """Le mouvement known_x402 doit remonter contract/token_symbol/resource/provider
    du spend matché, pour que l'alerte affiche QUEL token a été scanné et POURQUOI."""
    transfer = TokenTransfer(
        tx_hash="0xcybercentrypayment2", from_address=WALLET,
        to_address="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=0.02, timestamp="2026-07-22T16:43:55+00:00",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    known_x402_spends = [{
        "pay_to": "0xfEE13309251B632317ea2d475d6ABa7E7E0219e6",
        "amount_usd": 0.02, "status": "ok",
        "created_at": "2026-07-22T16:43:52.325464+00:00",
        "contract": "0xdeadbeef00000000000000000000000000dead",
        "token_symbol": "GEM", "resource": "honeypot_check", "provider": "GoPlus",
    }]

    result = await monitor.check_wallet_activity(
        wallet_address=WALLET, known_x402_spends=known_x402_spends,
    )

    assert len(result) == 1
    m = result[0]
    assert m.classification == "known_x402"
    assert m.contract == "0xdeadbeef00000000000000000000000000dead"
    assert m.token_symbol == "GEM"
    assert m.resource == "honeypot_check"
    assert m.provider == "GoPlus"


def test_format_movement_alert_known_x402_with_contract_shows_token_dexscreener_reason_basescan():
    from aria_core.agent_wallet_monitor import WalletMovement, basescan_tx_url, format_movement_alert

    m = WalletMovement(
        tx_hash="0xcybercentrypayment2", direction="out", asset="USDC", amount=0.02,
        counterparty="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6", classification="known_x402",
        contract="0xdeadbeef00000000000000000000000000dead", token_symbol="GEM",
        resource="honeypot_check", provider="GoPlus",
    )
    msg = format_movement_alert(m)
    # 22/07 (revue croisée) -- token_symbol affiché à côté de l'adresse quand connu.
    assert "Token : GEM (0xdeadbeef00000000000000000000000000dead)" in msg
    assert "DexScreener : https://dexscreener.com/base/0xdeadbeef00000000000000000000000000dead" in msg
    assert "Raison : honeypot_check via GoPlus" in msg
    assert f"BaseScan : {basescan_tx_url('0xcybercentrypayment2')}" in msg


def test_format_movement_alert_known_x402_without_token_symbol_shows_address_only():
    """token_symbol inconnu (spend loggé sans symbole) -- repli sur l'adresse seule,
    même format que le comportement historique avant l'ajout du symbole."""
    from aria_core.agent_wallet_monitor import WalletMovement, format_movement_alert

    m = WalletMovement(
        tx_hash="0xnosymbol", direction="out", asset="USDC", amount=0.02,
        counterparty="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6", classification="known_x402",
        contract="0xdeadbeef00000000000000000000000000dead", resource="honeypot_check",
    )
    msg = format_movement_alert(m)
    assert "Token : 0xdeadbeef00000000000000000000000000dead" in msg
    assert "Token : GEM" not in msg


def test_format_movement_alert_known_x402_empty_resource_omits_reason_line():
    """22/07 (revue croisée) -- contract renseigné mais resource vide (théorique,
    x402_budget.record_spend() l'exige en pratique, mais aucune garde ne le
    supposait avant ce correctif) : aucune ligne 'Raison : ' à moitié vide."""
    from aria_core.agent_wallet_monitor import WalletMovement, format_movement_alert

    m = WalletMovement(
        tx_hash="0xemptyreason", direction="out", asset="USDC", amount=0.02,
        counterparty="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6", classification="known_x402",
        contract="0xdeadbeef00000000000000000000000000dead", resource="", provider="GoPlus",
    )
    msg = format_movement_alert(m)
    assert "Raison" not in msg
    assert "Token : 0xdeadbeef00000000000000000000000000dead" in msg


def test_format_movement_alert_known_x402_reason_without_provider():
    from aria_core.agent_wallet_monitor import WalletMovement, format_movement_alert

    m = WalletMovement(
        tx_hash="0xabc", direction="out", asset="USDC", amount=0.02,
        counterparty="0xfEE13309251B632317ea2d475d6ABa7E7E0219e6", classification="known_x402",
        contract="0xdeadbeef00000000000000000000000000dead", resource="recherche web generique",
        provider="",
    )
    msg = format_movement_alert(m)
    assert "Raison : recherche web generique" in msg
    assert " via " not in msg


def test_format_movement_alert_known_x402_without_contract_adds_no_extra_lines():
    """Paiement x402 générique (ex. recherche web) sans contract associé -- aucune
    des 4 lignes (Token/DexScreener/Raison/BaseScan) ne doit apparaître, jamais un
    "N/A" ou une ligne vide."""
    from aria_core.agent_wallet_monitor import WalletMovement, format_movement_alert

    m = WalletMovement(
        tx_hash="0xwebpayment", direction="out", asset="USDC", amount=0.05,
        counterparty="0xsomeprovider", classification="known_x402",
        resource="recherche web generique", provider="Tavily",
        # contract volontairement vide -- pas de token concerné par ce paiement
    )
    msg = format_movement_alert(m)
    assert "Token :" not in msg
    assert "DexScreener :" not in msg
    assert "Raison :" not in msg
    assert "BaseScan :" not in msg
    assert "N/A" not in msg


def test_matches_known_x402_returns_full_spend_dict_on_match():
    spend = {
        "pay_to": "0xabc", "amount_usd": 0.02, "created_at": "2026-07-17T16:43:52Z",
        "contract": "0xgem", "token_symbol": "GEM", "resource": "honeypot_check", "provider": "GoPlus",
    }
    result = monitor._matches_known_x402(
        counterparty="0xabc", amount=0.02, timestamp="2026-07-17T16:44:00Z",
        known_x402_spends=[spend],
    )
    assert result == spend


def test_matches_known_x402_returns_none_on_no_match():
    result = monitor._matches_known_x402(
        counterparty="0xabc", amount=0.02, timestamp="2026-07-17T16:44:00Z",
        known_x402_spends=[{"pay_to": "0xdifferent", "amount_usd": 0.02, "created_at": "2026-07-17T16:43:52Z"}],
    )
    assert result is None


def test_basescan_tx_url_builds_expected_url():
    from aria_core.agent_wallet_monitor import basescan_tx_url

    assert basescan_tx_url("0xabc123") == "https://basescan.org/tx/0xabc123"


# ── non-régression : les autres classifications n'affichent RIEN de nouveau ────


def test_format_movement_alert_known_shows_no_extra_lines():
    m = monitor.WalletMovement(
        tx_hash="0xariainitiated", direction="out", asset="USDC", amount=5.0,
        counterparty="0x33783cCb570Cb279C25F836806B5c4C3C8309777", classification="known",
    )
    msg = monitor.format_movement_alert(m)
    assert msg == (
        "✅ Wallet agent — Mouvement initié par ARIA (attendu)\n"
        "Sortie : 5.0 USDC\n"
        "Vers : 0x33783cCb570Cb279C25F836806B5c4C3C8309777\n"
        "Tx : 0xariainitiated"
    )


def test_format_movement_alert_external_deposit_shows_no_extra_lines():
    m = monitor.WalletMovement(
        tx_hash="0xgood", direction="in", asset="USDC", amount=1.0,
        counterparty="0xoperator", classification="external_deposit",
    )
    msg = monitor.format_movement_alert(m)
    assert "BaseScan :" not in msg
    assert "Token :" not in msg
    assert "DexScreener :" not in msg
    assert "Raison :" not in msg


def test_format_movement_alert_unexpected_outflow_shows_no_extra_lines():
    m = monitor.WalletMovement(
        tx_hash="0xbad", direction="out", asset="USDC", amount=5.0,
        counterparty="0xunknown", classification="unexpected_outflow",
    )
    msg = monitor.format_movement_alert(m)
    assert "BaseScan :" not in msg
    assert "Token :" not in msg
    assert "DexScreener :" not in msg
    assert "Raison :" not in msg


def test_format_movement_alert_suspicious_token_shows_no_extra_lines():
    m = monitor.WalletMovement(
        tx_hash="0xfaketh", direction="in", asset="EṬH (FAUX ETH -- contrat non officiel)",
        amount=0.001, counterparty="0xscammer", classification="suspicious_token",
    )
    msg = monitor.format_movement_alert(m)
    assert "BaseScan :" not in msg
    assert "Token :" not in msg
    assert "DexScreener :" not in msg
    assert "Raison :" not in msg


@pytest.mark.asyncio
async def test_fake_eth_erc20_homoglyph_flagged_suspicious(monkeypatch):
    """Trouvé en conditions réelles (17/07) : un token ERC-20 nommé "EṬH" (T à
    point souscrit Unicode, U+0323) reçu sur le wallet agent -- visuellement
    indiscernable de "ETH" dans Telegram, mais ETH natif n'a JAMAIS de contrat
    ERC-20 légitime. Doit être classé suspect, jamais confondu avec un vrai dépôt."""
    transfer = TokenTransfer(
        tx_hash="0xfaketh", from_address="0xscammer", to_address=WALLET,
        token_address="0x7bbEa45b0ee287A5f9ce25eefEb0FFC334DA4be8",
        token_symbol="EṬH", token_name="EṬH",
        amount=0.001, timestamp="2026-07-17T00:15:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(result) == 1
    assert result[0].classification == "suspicious_token"
    assert "FAUX ETH" in result[0].asset


@pytest.mark.asyncio
async def test_fake_usdc_wrong_contract_flagged_suspicious(monkeypatch):
    """Même attaque que le token ETH, sur USDC (contrat différent du vrai
    ``USDC_BASE_ADDRESS`` malgré un symbole identique/similaire)."""
    transfer = TokenTransfer(
        tx_hash="0xfakeusdc", from_address="0xscammer2", to_address=WALLET,
        token_address="0x48FfB148167894E2aB1e273fDcd1aACA705bd6Ff",
        token_symbol="USḌC", token_name="USḌ Coin",
        amount=1.0, timestamp="2026-07-17T00:00:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(result) == 1
    assert result[0].classification == "suspicious_token"
    assert "FAUX USDC" in result[0].asset


@pytest.mark.asyncio
async def test_real_usdc_correct_contract_not_flagged(monkeypatch):
    """Non-régression : le vrai contrat USDC ne doit jamais être signalé suspect."""
    transfer = TokenTransfer(
        tx_hash="0xrealusdc", from_address="0xoperator", to_address=WALLET,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=1.0, timestamp="2026-07-17T00:00:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert result[0].classification == "external_deposit"
    assert result[0].asset == "USDC"


def test_format_movement_alert_suspicious_token_uses_distinct_icon():
    m = monitor.WalletMovement(
        tx_hash="0xfaketh", direction="in", asset="EṬH (FAUX ETH -- contrat non officiel)",
        amount=0.001, counterparty="0xscammer", classification="suspicious_token",
    )
    text = monitor.format_movement_alert(m)
    assert "🎣" in text
    assert "ne jamais interagir" in text.lower()


@pytest.mark.asyncio
async def test_native_eth_deposit_detected(monkeypatch):
    tx = Transaction(
        tx_hash="0xethdeposit", from_address="0xoperator", to_address=WALLET,
        value_native=0.001, status="ok", method=None, timestamp="2026-07-16T19:15:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        transactions=TransactionsResult(transactions=[tx], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(result) == 1
    assert result[0].asset == "ETH"
    assert result[0].classification == "external_deposit"


@pytest.mark.asyncio
async def test_native_tx_with_zero_value_ignored(monkeypatch):
    """Un appel de contrat sans transfert de valeur (ex. approve) n'est pas un
    mouvement de fonds -- ne doit jamais être journalisé comme tel."""
    tx = Transaction(
        tx_hash="0xapprove", from_address=WALLET, to_address="0xsomecontract",
        value_native=0.0, status="ok", method="approve", timestamp="2026-07-16T19:20:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        transactions=TransactionsResult(transactions=[tx], available=True),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert result == []


@pytest.mark.asyncio
async def test_same_tx_hash_never_detected_twice(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xrepeat", from_address="0xoperator", to_address=WALLET,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=1.0, timestamp="2026-07-16T19:25:00Z",
    )
    client = FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    )
    _patch_client(monkeypatch, client)
    first = await monitor.check_wallet_activity(wallet_address=WALLET)
    second = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert len(first) == 1
    assert second == []  # déjà vu, jamais renvoyé/journalisé une deuxième fois


@pytest.mark.asyncio
async def test_record_movement_returns_true_only_for_the_winning_write():
    """20/07 -- bug réel (capture opérateur) : alerte "SORTIE NON INITIÉE" sur un
    paiement x402 pourtant déjà connu -- l'ancien _record_movement n'indiquait
    jamais si CETTE tentative avait réellement gagné l'écriture, donc un passage
    dont la classification avait été calculée sur une lecture périmée de
    known_x402_spends notifiait quand même. Verrouille le contrat : True
    seulement pour la première écriture d'un tx_hash donné."""
    m1 = monitor.WalletMovement(
        tx_hash="0xrace", direction="out", asset="USDC", amount=0.006,
        counterparty="0xprovider", classification="known_x402",
    )
    m2 = monitor.WalletMovement(
        tx_hash="0xrace", direction="out", asset="USDC", amount=0.006,
        counterparty="0xprovider", classification="unexpected_outflow",
    )
    assert await monitor._record_movement(m1) is True
    assert await monitor._record_movement(m2) is False
    # La classification PERSISTÉE reste celle du gagnant -- jamais écrasée par
    # le perdant, même si celui-ci tente d'écrire une classification différente.
    rows = await monitor.list_recent_movements()
    assert len(rows) == 1
    assert rows[0]["classification"] == "known_x402"


@pytest.mark.asyncio
async def test_check_wallet_activity_never_returns_a_movement_that_lost_the_write(monkeypatch):
    """Le contrat au niveau de check_wallet_activity : un mouvement qui perd la
    course d'écriture ne doit jamais apparaître dans la liste renvoyée à
    l'appelant (donc jamais notifié) -- même s'il a été détecté par Blockscout."""
    transfer = TokenTransfer(
        tx_hash="0xalreadyclaimed", from_address=WALLET, to_address="0xdest",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=0.006, timestamp="2026-07-20T19:38:55Z",
    )
    client = FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    )
    _patch_client(monkeypatch, client)

    async def fake_record_movement(m):
        return False  # simule une course déjà perdue par un autre passage

    monkeypatch.setattr(monitor, "_record_movement", fake_record_movement)
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert result == []


@pytest.mark.asyncio
async def test_blockscout_unavailable_degrades_gracefully(monkeypatch):
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[], available=False, error="indisponible"),
        transactions=TransactionsResult(transactions=[], available=False, error="indisponible"),
    ))
    result = await monitor.check_wallet_activity(wallet_address=WALLET)
    assert result == []  # jamais une exception, dégradation silencieuse (loggée en interne)


@pytest.mark.asyncio
async def test_movements_persisted_and_listable(monkeypatch):
    transfer = TokenTransfer(
        tx_hash="0xpersisted", from_address="0xoperator", to_address=WALLET,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=2.5, timestamp="2026-07-16T19:30:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    await monitor.check_wallet_activity(wallet_address=WALLET)
    rows = await monitor.list_recent_movements()
    assert len(rows) == 1
    assert rows[0]["tx_hash"] == "0xpersisted"
    assert rows[0]["classification"] == "external_deposit"


def test_format_movement_alert_flags_unexpected_outflow_prominently():
    m = monitor.WalletMovement(
        tx_hash="0xbad", direction="out", asset="USDC", amount=5.0,
        counterparty="0xunknown", classification="unexpected_outflow",
    )
    text = monitor.format_movement_alert(m)
    assert "🚨" in text
    assert "vérifier immédiatement" in text.lower() or "SORTIE NON INITIÉE" in text


def test_format_movement_alert_deposit_uses_distinct_icon():
    m = monitor.WalletMovement(
        tx_hash="0xgood", direction="in", asset="USDC", amount=1.0,
        counterparty="0xoperator", classification="external_deposit",
    )
    text = monitor.format_movement_alert(m)
    assert "💰" in text


def test_agent_wallet_monitor_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", raising=False)
    assert monitor.agent_wallet_monitor_enabled() is False
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")
    assert monitor.agent_wallet_monitor_enabled() is True


@pytest.mark.asyncio
async def test_run_cycle_skipped_when_gate_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", raising=False)
    result = await monitor.run_agent_wallet_monitor_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_run_cycle_nothing_new_when_no_movement(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")
    _patch_client(monkeypatch, FakeBlockscoutClient())
    result = await monitor.run_agent_wallet_monitor_cycle()
    assert result == {"outcome": "nothing_new"}


@pytest.mark.asyncio
async def test_run_cycle_notifies_on_fresh_deposit(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")
    transfer = TokenTransfer(
        tx_hash="0xcycle1", from_address="0xoperator", to_address=monitor.MONITORED_WALLET_ADDRESS,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=1.0, timestamp="2026-07-16T20:00:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    sent = []

    async def _notifier(text):
        sent.append(text)

    result = await monitor.run_agent_wallet_monitor_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert result["detected"] == 1
    assert result["notified"] == 1
    assert len(sent) == 1
    assert "💰" in sent[0]


@pytest.mark.asyncio
async def test_run_cycle_does_not_notify_when_killswitch_paused(monkeypatch):
    """Le kill-switch coupe la NOTIFICATION, jamais la lecture/journalisation --
    le registre reste complet meme en pause."""
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")
    transfer = TokenTransfer(
        tx_hash="0xcycle2", from_address="0xoperator", to_address=monitor.MONITORED_WALLET_ADDRESS,
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=1.0, timestamp="2026-07-16T20:05:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))

    from aria_core import outgoing_pause

    monkeypatch.setattr(outgoing_pause, "is_paused", lambda *a, **k: True)
    sent = []

    async def _notifier(text):
        sent.append(text)

    result = await monitor.run_agent_wallet_monitor_cycle(notifier=_notifier)
    assert result["outcome"] == "ok"
    assert result["detected"] == 1
    assert result["notified"] == 0
    assert sent == []
    rows = await monitor.list_recent_movements()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_run_cycle_flags_unexpected_outflow_count(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")
    transfer = TokenTransfer(
        tx_hash="0xcycle3", from_address=monitor.MONITORED_WALLET_ADDRESS, to_address="0xunknown",
        token_address=USDC_ADDR, token_symbol="USDC", token_name="USD Coin",
        amount=5.0, timestamp="2026-07-16T20:10:00Z",
    )
    _patch_client(monkeypatch, FakeBlockscoutClient(
        token_transfers=TokenTransfersResult(transfers=[transfer], available=True),
    ))
    result = await monitor.run_agent_wallet_monitor_cycle(notifier=None)
    assert result["outcome"] == "ok"
    assert result["unexpected_outflows"] == 1


@pytest.mark.asyncio
async def test_run_cycle_error_on_check_activity_failure(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_MONITOR_ENABLED", "true")

    async def _raise(*a, **k):
        raise RuntimeError("blockscout down")

    monkeypatch.setattr(monitor, "check_wallet_activity", _raise)
    result = await monitor.run_agent_wallet_monitor_cycle()
    assert result["outcome"] == "error"


def _fake_list_all_token_balances(tokens):
    async def _fake(*, network="base"):
        return tokens
    return _fake


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_returns_both_balances(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances(
            [{"address": adapter.USDC_BASE_ADDRESS, "symbol": "USDC", "amount": 12.5}]
        ),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.002, available=True),
    ))
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["usdc_usd"] == 12.5
    assert result["eth"] == 0.002
    assert result["wallet_address"] == WALLET
    assert result["other_tokens"] == []


class _FakePairSnapshot:
    def __init__(self, *, base_address, price_usd, liquidity_usd=100_000.0):
        self.base_address = base_address
        self.price_usd = price_usd
        self.liquidity_usd = liquidity_usd


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_includes_other_tokens(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": adapter.USDC_BASE_ADDRESS, "symbol": "USDC", "amount": 5.0},
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.0, available=True),
    ))

    async def fake_fetch_tokens_batch(addresses, *, chain="base"):
        return [_FakePairSnapshot(base_address="0xdeadbeef", price_usd=2.5)]

    monkeypatch.setattr(
        "aria_core.services.dexscreener.fetch_tokens_batch", fake_fetch_tokens_batch,
    )
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["usdc_usd"] == 5.0
    assert result["other_tokens"] == [
        {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0, "price_usd": 2.5, "value_usd": 105.0}
    ]


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_other_tokens_price_unavailable(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.0, available=True),
    ))

    async def fake_fetch_tokens_batch(addresses, *, chain="base"):
        return []  # aucun pool trouve pour ce token

    monkeypatch.setattr(
        "aria_core.services.dexscreener.fetch_tokens_batch", fake_fetch_tokens_batch,
    )
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    token = result["other_tokens"][0]
    assert token["price_usd"] is None
    assert token["value_usd"] is None


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_picks_highest_liquidity_pair(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 10.0},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.0, available=True),
    ))

    async def fake_fetch_tokens_batch(addresses, *, chain="base"):
        return [
            _FakePairSnapshot(base_address="0xdeadbeef", price_usd=1.0, liquidity_usd=500.0),
            _FakePairSnapshot(base_address="0xdeadbeef", price_usd=9.0, liquidity_usd=999_999.0),
        ]

    monkeypatch.setattr(
        "aria_core.services.dexscreener.fetch_tokens_batch", fake_fetch_tokens_batch,
    )
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["other_tokens"][0]["price_usd"] == 9.0


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_degrades_when_price_lookup_raises(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.0, available=True),
    ))

    async def fake_fetch_tokens_batch(addresses, *, chain="base"):
        raise RuntimeError("dexscreener down")

    monkeypatch.setattr(
        "aria_core.services.dexscreener.fetch_tokens_batch", fake_fetch_tokens_batch,
    )
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    token = result["other_tokens"][0]
    assert token["amount"] == 42.0
    assert token["value_usd"] is None


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_defaults_to_monitored_address(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=monitor.MONITORED_WALLET_ADDRESS, balance_native=0.0, available=True),
    ))
    result = await monitor.get_wallet_balance_summary()
    assert result["wallet_address"] == monitor.MONITORED_WALLET_ADDRESS


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_uses_cdp_eth_as_fallback_when_blockscout_fails(monkeypatch):
    """Confirme en direct le 16/07 (/agentwallet) : list_all_token_balances renvoie
    aussi l'ETH natif -- jamais affiché comme "autre token", utilisé en repli
    quand Blockscout echoue."""
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": adapter.USDC_BASE_ADDRESS, "symbol": "USDC", "amount": 1.0},
            {"address": "0xeth", "symbol": "ETH", "amount": 0.001},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, available=False, error="indisponible"),
    ))
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["eth"] == 0.001
    assert result["other_tokens"] == []


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_prefers_blockscout_eth_over_cdp_fallback(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances([
            {"address": "0xeth", "symbol": "ETH", "amount": 0.001},
        ]),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.002, available=True),
    ))
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["eth"] == 0.002


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_degrades_honestly_when_usdc_unavailable(monkeypatch):
    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances",
        _fake_list_all_token_balances(None),
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, available=False, error="indisponible"),
    ))
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["usdc_usd"] is None
    assert result["eth"] is None
    assert result["other_tokens"] is None


@pytest.mark.asyncio
async def test_get_wallet_balance_summary_degrades_when_cdp_adapter_raises(monkeypatch):
    async def fake_list_all_token_balances(*, network="base"):
        raise RuntimeError("cdp-sdk not installed")

    monkeypatch.setattr(
        "aria_core.agent_wallet_cdp_adapter.list_all_token_balances", fake_list_all_token_balances,
    )
    _patch_client(monkeypatch, FakeBlockscoutClientWithAddressInfo(
        AddressInfo(address=WALLET, balance_native=0.001, available=True),
    ))
    result = await monitor.get_wallet_balance_summary(wallet_address=WALLET)
    assert result["usdc_usd"] is None
    assert result["eth"] == 0.001


class FakeBlockscoutClientWithAddressInfo:
    def __init__(self, info: AddressInfo):
        self._info = info

    async def get_address_info(self, address):
        return self._info


def test_format_wallet_balance_summary_shows_both_balances():
    text = monitor.format_wallet_balance_summary(
        {"wallet_address": WALLET, "chain": "base", "usdc_usd": 3.5, "eth": 0.0021, "other_tokens": []}
    )
    assert "3.5000 USDC" in text
    assert "0.002100 ETH" in text
    assert WALLET in text
    assert "Autres tokens : aucun" in text


def test_format_wallet_balance_summary_lists_other_tokens_with_usd_value():
    text = monitor.format_wallet_balance_summary({
        "wallet_address": WALLET, "chain": "base", "usdc_usd": 3.5, "eth": 0.0021,
        "other_tokens": [
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0, "price_usd": 2.5, "value_usd": 105.0}
        ],
    })
    assert "42.0 SOMEGEM" in text
    assert "105.00 $" in text


def test_format_wallet_balance_summary_shows_price_unavailable_per_token():
    text = monitor.format_wallet_balance_summary({
        "wallet_address": WALLET, "chain": "base", "usdc_usd": 3.5, "eth": 0.0021,
        "other_tokens": [
            {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0, "price_usd": None, "value_usd": None}
        ],
    })
    assert "42.0 SOMEGEM" in text
    assert "prix indisponible" in text


def test_format_wallet_balance_summary_degrades_honestly():
    text = monitor.format_wallet_balance_summary(
        {"wallet_address": WALLET, "chain": "base", "usdc_usd": None, "eth": None, "other_tokens": None}
    )
    assert "indisponible" in text.lower()
