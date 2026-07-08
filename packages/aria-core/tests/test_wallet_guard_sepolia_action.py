"""Câblage de l'action onchain_anchor_sepolia dans WALLET_ACTIONS (exécuteur, pas l'escalade)."""
from __future__ import annotations

from aria_core import wallet_guard as wg


def test_onchain_anchor_sepolia_registered():
    assert "onchain_anchor_sepolia" in wg.WALLET_ACTIONS


def test_executor_returns_tx_hash_on_success(monkeypatch):
    monkeypatch.setattr(
        "aria_core.onchain.sepolia_wallet.send_anchor_transaction",
        lambda **kw: "0xdeadbeef",
    )
    row, err = wg.WALLET_ACTIONS["onchain_anchor_sepolia"](
        {"contract": "0xledger", "root": "0x" + "ab" * 32, "chain_id": 84532}
    )
    assert err is None
    assert row == {"tx_hash": "0xdeadbeef"}


def test_executor_returns_error_string_on_failure(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("refusé : chain_id 8453 != Sepolia (84532)")

    monkeypatch.setattr("aria_core.onchain.sepolia_wallet.send_anchor_transaction", _boom)
    row, err = wg.WALLET_ACTIONS["onchain_anchor_sepolia"](
        {"contract": "0xledger", "root": "0x" + "ab" * 32, "chain_id": 8453}
    )
    assert row is None
    assert "Sepolia" in err
