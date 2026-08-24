"""18/08 -- real signing module for the Robinhood Chain leg of the homemade
agent wallet. Verified live once against the real testnet before this file
was written (see docs/HANDOFF_AGENT_WALLET.md), but these automated tests
never touch the network -- same fake-w3 injection doctrine as
test_safe_robinhood_wallet.py/test_safe_robinhood_simulation.py, plus a real
(offline) eth_account keypair for the key-loading/signing path."""
from __future__ import annotations

import json

import pytest
from eth_account import Account

from aria_core.onchain import safe_robinhood_signer as signer
from aria_core.onchain import safe_robinhood_wallet as srw

SAFE = "0x" + "22" * 20
TOKEN = "0x" + "44" * 20
TO = "0x" + "55" * 20


@pytest.fixture
def delegate_account():
    return Account.create()


@pytest.fixture
def delegate_key_file(tmp_path, delegate_account):
    path = tmp_path / "delegate.json"
    path.write_text(json.dumps({
        "address": delegate_account.address, "private_key": delegate_account.key.hex(),
    }))
    return str(path)


# ── _load_delegate_key ──────────────────────────────────────────────────

def test_load_delegate_key_happy_path(delegate_key_file, delegate_account):
    address, account = signer._load_delegate_key(delegate_key_file)
    assert address == delegate_account.address
    assert account.address == delegate_account.address


def test_load_delegate_key_rejects_empty_path():
    with pytest.raises(signer.DelegateKeyError, match="aucun chemin"):
        signer._load_delegate_key("")


def test_load_delegate_key_rejects_missing_file(tmp_path):
    with pytest.raises(signer.DelegateKeyError, match="illisible"):
        signer._load_delegate_key(str(tmp_path / "does_not_exist.json"))


def test_load_delegate_key_rejects_missing_private_key_field(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"address": "0x" + "11" * 20}))
    with pytest.raises(signer.DelegateKeyError, match="private_key"):
        signer._load_delegate_key(str(path))


def test_load_delegate_key_rejects_address_mismatch(tmp_path, delegate_account):
    other = Account.create()
    path = tmp_path / "mismatched.json"
    path.write_text(json.dumps({
        "address": other.address, "private_key": delegate_account.key.hex(),
    }))
    with pytest.raises(signer.DelegateKeyError, match="ne correspond pas"):
        signer._load_delegate_key(str(path))


# ── send_allowance_transfer ─────────────────────────────────────────────

class _FakeFunctionCall:
    def __init__(self, base_tx):
        self._base_tx = base_tx

    def build_transaction(self, params):
        tx = dict(self._base_tx)
        tx.update(params)
        tx.setdefault("to", TO)
        tx.setdefault("value", 0)
        tx.setdefault("data", b"")
        tx.setdefault("gas", 100_000)
        tx.setdefault("gasPrice", 10_000_000)
        return tx


class _FakeAllowanceFunctions:
    def executeAllowanceTransfer(self, *args):
        return _FakeFunctionCall({})


class _FakeAllowanceContract:
    def __init__(self):
        self.functions = _FakeAllowanceFunctions()


class _FakeReceipt:
    def __init__(self, status):
        self.status = status


class _FakeEth:
    def __init__(self, *, chain_id, tx_count=0, send_should_fail=False, receipt_status=1):
        self.chain_id = chain_id
        self._tx_count = tx_count
        self._send_should_fail = send_should_fail
        self._receipt_status = receipt_status
        self.sent_raw = None

    def contract(self, address=None, abi=None):
        return _FakeAllowanceContract()

    def get_transaction_count(self, address):
        return self._tx_count

    def send_raw_transaction(self, raw):
        if self._send_should_fail:
            raise RuntimeError("RPC send failed (simulated)")
        self.sent_raw = raw
        return b"\xab" * 32

    def wait_for_transaction_receipt(self, tx_hash, timeout=60):
        return _FakeReceipt(self._receipt_status)


class _FakeW3:
    def __init__(self, **kw):
        self.eth = _FakeEth(**kw)


def _live_allowance(*, remaining, nonce=1):
    return {
        "error": None, "amount": remaining + 1_000, "spent": 1_000,
        "remaining": remaining, "nonce": nonce,
    }


@pytest.mark.asyncio
async def test_send_allowance_transfer_happy_path(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=1_000_000)
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=500_000,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is None
    assert result["status"] == "ok"
    assert result["tx_hash"].startswith("0x")
    assert w3.eth.sent_raw is not None


@pytest.mark.asyncio
async def test_send_allowance_transfer_rejects_over_remaining_allowance(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=100)
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=101,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is not None
    assert "allowance restante réelle" in result["error"]
    assert result["tx_hash"] is None
    assert w3.eth.sent_raw is None  # never even attempted a send


@pytest.mark.asyncio
async def test_send_allowance_transfer_never_trusts_a_stale_remaining(monkeypatch, delegate_key_file):
    """The whole point of re-reading on-chain inside this function: even if a
    caller believed a large allowance was available, the FRESH on-chain read
    is what gates the send."""
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=0)
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=1,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is not None
    assert w3.eth.sent_raw is None


@pytest.mark.asyncio
async def test_send_allowance_transfer_reports_allowance_read_failure(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: {"error": "RPC down", "remaining": None}
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=1,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is not None
    assert "allowance réelle illisible" in result["error"]


@pytest.mark.asyncio
async def test_send_allowance_transfer_refuses_wrong_chain(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=1_000_000)
    )
    w3 = _FakeW3(chain_id=4663)  # mainnet -- must be refused
    with pytest.raises(RuntimeError, match="refus"):
        await signer.send_allowance_transfer(
            safe=SAFE, token=TOKEN, to=TO, amount=1,
            delegate_key_path=delegate_key_file, w3=w3,
        )


@pytest.mark.asyncio
async def test_send_allowance_transfer_reports_send_failure(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=1_000_000)
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID, send_should_fail=True)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=500_000,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is not None
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_send_allowance_transfer_accepts_an_injected_account_instead_of_a_key_file(monkeypatch):
    """24/08 -- the heartbeat rehearsal cycle injects an already-loaded
    account (from ``safe_robinhood_deploy.deployer_account()``, itself
    reading this dome's existing testnet-only env var) rather than writing a
    second copy of the same key material to a JSON file on disk."""
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=1_000_000)
    )
    account = Account.create()
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=500_000, account=account, w3=w3,
    )
    assert result["error"] is None
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_send_allowance_transfer_rejects_both_account_and_key_path(delegate_key_file):
    account = Account.create()
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=1,
        delegate_key_path=delegate_key_file, account=account, w3=w3,
    )
    assert result["error"] is not None
    assert "exactement un" in result["error"]


@pytest.mark.asyncio
async def test_send_allowance_transfer_rejects_neither_account_nor_key_path():
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID)
    result = await signer.send_allowance_transfer(safe=SAFE, token=TOKEN, to=TO, amount=1, w3=w3)
    assert result["error"] is not None
    assert "exactement un" in result["error"]


@pytest.mark.asyncio
async def test_send_allowance_transfer_reports_reverted_receipt(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_allowance", lambda *a, **kw: _live_allowance(remaining=1_000_000)
    )
    w3 = _FakeW3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID, receipt_status=0)
    result = await signer.send_allowance_transfer(
        safe=SAFE, token=TOKEN, to=TO, amount=500_000,
        delegate_key_path=delegate_key_file, w3=w3,
    )
    assert result["error"] is None  # the SEND itself succeeded
    assert result["status"] == "reverted"  # but the chain rejected it
