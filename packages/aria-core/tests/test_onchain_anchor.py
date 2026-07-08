"""Ancrage onchain (Base) — préparation seulement, gaté OFF, serveur sans clé (dôme)."""
from __future__ import annotations

import re

from aria_core.onchain import anchor
from aria_core.onchain.attestation import merkle_root

_RECORDS = [
    {"contract": "0xabc", "verdict": "buy", "ts": "2026-07-08T10:00:00Z"},
    {"contract": "0xdef", "verdict": "avoid", "ts": "2026-07-08T11:00:00Z"},
]
_BYTES32 = re.compile(r"^0x[0-9a-f]{64}$")


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_ONCHAIN_ANCHOR_ENABLED", raising=False)
    assert anchor.anchor_enabled() is False
    assert anchor.build_anchor_request(_RECORDS) is None


def test_needs_ledger_address(monkeypatch):
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.delenv("ARIA_LEDGER_ADDRESS", raising=False)
    assert anchor.build_anchor_request(_RECORDS) is None


def test_no_records_is_none(monkeypatch):
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LEDGER_ADDRESS", "0xLedger")
    assert anchor.build_anchor_request([]) is None


def test_build_request_ok(monkeypatch):
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LEDGER_ADDRESS", "0xLedger")
    monkeypatch.delenv("ARIA_ONCHAIN_CHAIN_ID", raising=False)
    req = anchor.build_anchor_request(_RECORDS)
    assert req is not None
    assert req.chain_id == 8453 and req.network == "base"
    assert req.function == "anchor" and req.contract == "0xLedger"
    assert req.record_count == 2
    # La racine correspond exactement a l'attestation existante (pas de duplication).
    assert req.root == merkle_root(_RECORDS)
    assert _BYTES32.match(req.root), "la racine doit etre un bytes32 (0x + 64 hex)"
    d = req.as_dict()
    assert d["requiresLocalSigning"] is True and d["args"] == [req.root]
    instr = req.as_operator_instruction()
    assert "0xLedger" in instr and req.root in instr and "LOCALE" in instr


def test_chain_id_override(monkeypatch):
    # Permet Base Sepolia (testnet) avant le mainnet.
    monkeypatch.setenv("ARIA_ONCHAIN_ANCHOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LEDGER_ADDRESS", "0xLedger")
    monkeypatch.setenv("ARIA_ONCHAIN_CHAIN_ID", "84532")
    req = anchor.build_anchor_request(_RECORDS)
    assert req is not None and req.chain_id == 84532


def test_server_holds_no_key_and_never_sends():
    """Invariant dome : le module ne signe pas et n'emet aucun appel reseau (cle hors serveur)."""
    import inspect

    src = inspect.getsource(anchor)
    for forbidden in ("private_key", "send_raw_transaction", "eth_account", "import httpx", "requests."):
        assert forbidden not in src, f"le serveur d'ancrage ne doit pas contenir '{forbidden}'"
