"""Surveillance read-only du wallet agent (16/07, demande opérateur : détection
automatique des dépôts/retraits + registre complet) -- réutilise Blockscout
(déjà construit), aucun appel réseau réel ici, seulement des fakes injectés."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_monitor as monitor
from aria_core.services.blockscout import (
    Transaction,
    TransactionsResult,
    TokenTransfer,
    TokenTransfersResult,
)

WALLET = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"


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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
        token_address="0xusdc", token_symbol="USDC", token_name="USD Coin",
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
