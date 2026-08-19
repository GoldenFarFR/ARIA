"""19/08 -- real signing module for the Solana (Squads v4) leg of the
homemade agent wallet. Verified live against the real devnet before this
file was written (see docs/HANDOFF_AGENT_WALLET.md), but these automated
tests never touch the network -- same fake-client/monkeypatch injection
doctrine as test_safe_robinhood_signer.py, plus a real (offline) solders
Keypair for the key-loading/signing path."""
from __future__ import annotations

import json

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from aria_core.onchain import squads_solana_signer as signer

# Real devnet addresses this module was proven against live (18/08 milestone,
# re-derived and cross-checked this session) -- used here only as FIXED,
# deterministic expected values for the PDA-derivation regression tests,
# never contacted over the network by the automated suite.
_MULTISIG_CREATE_KEY = "Ec5sJzzV19zRJm3ZfV9YWbajdSxhpfyAJoVFYZM1US9c"
_SPENDING_LIMIT_CREATE_KEY = "2iWy14zqGGXiMf4PpctFbjPm7hmMMxroCK634VwMqzm5"
_EXPECTED_MULTISIG_PDA = "2FdX4cwkbDZ96kfM69agPxyYCCU9z9o91zLusFctbHGy"
_EXPECTED_SPENDING_LIMIT_PDA = "pcdcsyywGo2kJaV9exuwHGoAdENcs6L3RaVYPinx1Q5"
_EXPECTED_VAULT_PDA_0 = "2bv2z7K2ZP3vmRYVzWC4fQQmAtKqr5Ww9w4jqi79cJpp"

DESTINATION = "11111111111111111111111111111111"  # System Program id, valid base58, unused for PDA math


@pytest.fixture
def delegate_keypair():
    return Keypair()


@pytest.fixture
def delegate_key_file(tmp_path, delegate_keypair):
    path = tmp_path / "delegate.json"
    path.write_text(json.dumps(list(bytes(delegate_keypair))))
    return str(path)


# ── _load_delegate_key ──────────────────────────────────────────────────

def test_load_delegate_key_happy_path(delegate_key_file, delegate_keypair):
    address, keypair = signer._load_delegate_key(delegate_key_file)
    assert address == str(delegate_keypair.pubkey())
    assert keypair.pubkey() == delegate_keypair.pubkey()


def test_load_delegate_key_rejects_empty_path():
    with pytest.raises(signer.DelegateKeyError, match="aucun chemin"):
        signer._load_delegate_key("")


def test_load_delegate_key_rejects_missing_file(tmp_path):
    with pytest.raises(signer.DelegateKeyError, match="illisible"):
        signer._load_delegate_key(str(tmp_path / "does_not_exist.json"))


def test_load_delegate_key_rejects_wrong_shape(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"address": "not-an-array"}))
    with pytest.raises(signer.DelegateKeyError, match="format de clé"):
        signer._load_delegate_key(str(path))


def test_load_delegate_key_rejects_wrong_length(tmp_path):
    path = tmp_path / "short.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(signer.DelegateKeyError, match="format de clé"):
        signer._load_delegate_key(str(path))


def test_load_delegate_key_rejects_invalid_bytes(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps([0] * 64))
    with pytest.raises(signer.DelegateKeyError, match="invalide"):
        signer._load_delegate_key(str(path))


# ── PDA derivation -- pinned against the real devnet accounts (18/08) ──────

def test_derive_multisig_pda_matches_real_devnet_account():
    pda, bump = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    assert str(pda) == _EXPECTED_MULTISIG_PDA
    assert bump == 255


def test_derive_spending_limit_pda_matches_real_devnet_account():
    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    pda, bump = signer.derive_spending_limit_pda(
        multisig_pda, Pubkey.from_string(_SPENDING_LIMIT_CREATE_KEY),
    )
    assert str(pda) == _EXPECTED_SPENDING_LIMIT_PDA
    assert bump == 255


def test_derive_vault_pda_matches_real_devnet_account():
    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    pda, bump = signer.derive_vault_pda(multisig_pda, 0)
    assert str(pda) == _EXPECTED_VAULT_PDA_0
    assert bump == 255


def test_derive_vault_pda_rejects_out_of_range_index():
    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    with pytest.raises(ValueError, match="hors bornes"):
        signer.derive_vault_pda(multisig_pda, 256)


# ── read_spending_limit -- decoded against the REAL byte layout seen live ──

def _fake_spending_limit_account_data() -> str:
    """Builds the exact raw bytes independently re-read from the real
    devnet SpendingLimit account this session (amount=3_000_000,
    remaining_amount=2_000_000, vault_index=0, period=OneTime, mint=SOL)."""
    import base64

    raw = bytearray(131)
    raw[8:40] = bytes(32)  # multisig (unused by the decoder)
    raw[40:72] = bytes(32)  # create_key (unused)
    raw[72] = 0  # vault_index
    raw[73:105] = bytes(32)  # mint == default (SOL)
    raw[105:113] = (3_000_000).to_bytes(8, "little")  # amount
    raw[113] = 0  # period = OneTime
    raw[114:122] = (2_000_000).to_bytes(8, "little")  # remaining_amount
    raw[122:130] = (1_787_068_453).to_bytes(8, "little", signed=True)  # last_reset
    raw[130] = 255  # bump
    return base64.b64encode(bytes(raw)).decode("ascii")


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload=None, *, raises=None):
        self._payload = payload
        self._raises = raises

    def post(self, url, json, timeout):
        if self._raises is not None:
            raise self._raises
        return _FakeHttpResponse(self._payload)


def test_read_spending_limit_happy_path():
    client = _FakeHttpClient({
        "result": {"value": {"data": [_fake_spending_limit_account_data(), "base64"]}},
    })
    result = signer.read_spending_limit(spending_limit_pda=_EXPECTED_SPENDING_LIMIT_PDA, client=client)
    assert result["error"] is None
    assert result["amount"] == 3_000_000
    assert result["remaining_amount"] == 2_000_000
    assert result["period"] == 0
    assert result["mint_is_sol"] is True


def test_read_spending_limit_flags_missing_account():
    client = _FakeHttpClient({"result": {"value": None}})
    result = signer.read_spending_limit(spending_limit_pda=_EXPECTED_SPENDING_LIMIT_PDA, client=client)
    assert result["error"] is not None
    assert result["remaining_amount"] is None


def test_read_spending_limit_never_raises_on_rpc_failure():
    client = _FakeHttpClient(raises=ConnectionError("RPC unreachable"))
    result = signer.read_spending_limit(spending_limit_pda=_EXPECTED_SPENDING_LIMIT_PDA, client=client)
    assert result["error"] is not None
    assert result["remaining_amount"] is None


# ── build_spending_limit_use_instruction ────────────────────────────────

def test_build_instruction_discriminator_matches_anchor_convention():
    import hashlib

    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    spending_limit_pda, _ = signer.derive_spending_limit_pda(
        multisig_pda, Pubkey.from_string(_SPENDING_LIMIT_CREATE_KEY),
    )
    vault_pda, _ = signer.derive_vault_pda(multisig_pda, 0)
    member = Pubkey.new_unique()

    instr = signer.build_spending_limit_use_instruction(
        multisig_pda=multisig_pda, member=member, spending_limit_pda=spending_limit_pda,
        vault_pda=vault_pda, destination=Pubkey.new_unique(), amount=100_000,
    )
    expected_discriminator = hashlib.sha256(b"global:spending_limit_use").digest()[:8]
    assert bytes(instr.data)[:8] == expected_discriminator


def test_build_instruction_account_order_and_flags():
    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    spending_limit_pda, _ = signer.derive_spending_limit_pda(
        multisig_pda, Pubkey.from_string(_SPENDING_LIMIT_CREATE_KEY),
    )
    vault_pda, _ = signer.derive_vault_pda(multisig_pda, 0)
    member = Pubkey.new_unique()
    destination = Pubkey.new_unique()

    instr = signer.build_spending_limit_use_instruction(
        multisig_pda=multisig_pda, member=member, spending_limit_pda=spending_limit_pda,
        vault_pda=vault_pda, destination=destination, amount=100_000,
    )
    accounts = instr.accounts
    assert len(accounts) == 10
    assert accounts[0].pubkey == multisig_pda and not accounts[0].is_signer and not accounts[0].is_writable
    assert accounts[1].pubkey == member and accounts[1].is_signer and not accounts[1].is_writable
    assert accounts[2].pubkey == spending_limit_pda and accounts[2].is_writable
    assert accounts[3].pubkey == vault_pda and accounts[3].is_writable
    assert accounts[4].pubkey == destination and accounts[4].is_writable
    # 5 = systemProgram (real, SOL case) -- 6..9 = the 4 SPL-only optional
    # accounts, all sentinelled with the program's own id.
    program_id = Pubkey.from_string(signer.SQUADS_V4_PROGRAM_ID)
    for idx in (6, 7, 8, 9):
        assert accounts[idx].pubkey == program_id
        assert not accounts[idx].is_signer
    assert accounts[7].is_writable  # vaultTokenAccount, isMut=true per IDL
    assert accounts[8].is_writable  # destinationTokenAccount, isMut=true per IDL
    assert not accounts[6].is_writable  # mint, isMut=false per IDL
    assert not accounts[9].is_writable  # tokenProgram, isMut=false per IDL


def test_build_instruction_args_encoding_with_and_without_memo():
    multisig_pda, _ = signer.derive_multisig_pda(Pubkey.from_string(_MULTISIG_CREATE_KEY))
    spending_limit_pda, _ = signer.derive_spending_limit_pda(
        multisig_pda, Pubkey.from_string(_SPENDING_LIMIT_CREATE_KEY),
    )
    vault_pda, _ = signer.derive_vault_pda(multisig_pda, 0)

    instr_no_memo = signer.build_spending_limit_use_instruction(
        multisig_pda=multisig_pda, member=Pubkey.new_unique(), spending_limit_pda=spending_limit_pda,
        vault_pda=vault_pda, destination=Pubkey.new_unique(), amount=250_000, decimals=9,
    )
    data = bytes(instr_no_memo.data)[8:]  # skip discriminator
    assert data[:8] == (250_000).to_bytes(8, "little")
    assert data[8] == 9
    assert data[9] == 0  # memo option tag: None

    instr_with_memo = signer.build_spending_limit_use_instruction(
        multisig_pda=multisig_pda, member=Pubkey.new_unique(), spending_limit_pda=spending_limit_pda,
        vault_pda=vault_pda, destination=Pubkey.new_unique(), amount=1, decimals=9, memo="aria",
    )
    data2 = bytes(instr_with_memo.data)[8:]
    assert data2[9] == 1  # memo option tag: Some
    length = int.from_bytes(data2[10:14], "little")
    assert length == 4
    assert data2[14:14 + length] == b"aria"


# ── send_spending_limit_transfer ────────────────────────────────────────

def _live_spending_limit(*, remaining):
    return {
        "error": None, "amount": remaining + 1_000_000, "remaining_amount": remaining,
        "period": 0, "vault_index": 0, "mint_is_sol": True, "last_reset": 0, "bump": 255,
    }


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_happy_path(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: _live_spending_limit(remaining=1_000_000),
    )
    sent = {}

    def _fake_send(instruction, keypair, *, client=None, wait_for_confirmation=True):
        sent["instruction"] = instruction
        return {"error": None, "tx_hash": "5FakeSig", "status": "ok"}

    monkeypatch.setattr(signer, "_sign_and_send_instruction", _fake_send)

    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=500_000,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] is None
    assert result["status"] == "ok"
    assert result["tx_hash"] == "5FakeSig"
    assert "instruction" in sent


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_rejects_over_remaining(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: _live_spending_limit(remaining=100),
    )

    def _fake_send(*a, **kw):
        raise AssertionError("must never attempt a send when over the real remaining amount")

    monkeypatch.setattr(signer, "_sign_and_send_instruction", _fake_send)

    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=101,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] is not None
    assert "spending limit restante réelle" in result["error"]
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_never_trusts_a_stale_remaining(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: _live_spending_limit(remaining=0),
    )

    def _fake_send(*a, **kw):
        raise AssertionError("must never send with zero real remaining")

    monkeypatch.setattr(signer, "_sign_and_send_instruction", _fake_send)

    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=1,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] is not None
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_reports_read_failure(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: {"error": "RPC down", "remaining_amount": None},
    )
    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=1,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] is not None
    assert "spending limit réelle illisible" in result["error"]


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_propagates_send_failure(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: _live_spending_limit(remaining=1_000_000),
    )
    monkeypatch.setattr(
        signer, "_sign_and_send_instruction",
        lambda *a, **kw: {"error": "send failed (simulated)", "tx_hash": None},
    )
    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=1,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] == "send failed (simulated)"
    assert result["tx_hash"] is None


@pytest.mark.asyncio
async def test_send_spending_limit_transfer_propagates_reverted_status(monkeypatch, delegate_key_file):
    monkeypatch.setattr(
        signer, "read_spending_limit", lambda **kw: _live_spending_limit(remaining=1_000_000),
    )
    monkeypatch.setattr(
        signer, "_sign_and_send_instruction",
        lambda *a, **kw: {"error": None, "tx_hash": "5RevertedSig", "status": "reverted (6026)"},
    )
    result = await signer.send_spending_limit_transfer(
        multisig_create_key=_MULTISIG_CREATE_KEY,
        spending_limit_create_key=_SPENDING_LIMIT_CREATE_KEY,
        vault_index=0, destination=DESTINATION, amount=1,
        delegate_key_path=delegate_key_file,
    )
    assert result["error"] is None  # the SEND itself succeeded
    assert result["status"].startswith("reverted")  # but the chain rejected it


# ── _await_confirmation ──────────────────────────────────────────────────

def test_await_confirmation_reports_ok():
    client = _FakeHttpClient({
        "result": {"value": [{"err": None, "confirmationStatus": "finalized"}]},
    })
    status = signer._await_confirmation("5Sig", client=client, timeout_s=5, poll_interval_s=0.01)
    assert status == "ok"


def test_await_confirmation_does_not_treat_merely_confirmed_as_ok():
    """Real race found live 19/08: a plain getAccountInfo read immediately
    after 'confirmed' (not yet 'finalized') can still see stale on-chain
    data -- 'confirmed' alone must never be reported as the settled 'ok'
    status a caller then trusts to read fresh state from."""
    client = _FakeHttpClient({
        "result": {"value": [{"err": None, "confirmationStatus": "confirmed"}]},
    })
    status = signer._await_confirmation("5Sig", client=client, timeout_s=0.05, poll_interval_s=0.01)
    assert status.startswith("unknown")


def test_await_confirmation_reports_reverted():
    client = _FakeHttpClient({
        "result": {"value": [{"err": {"InstructionError": [0, {"Custom": 6026}]}}]},
    })
    status = signer._await_confirmation("5Sig", client=client, timeout_s=5, poll_interval_s=0.01)
    assert status.startswith("reverted")
    assert "6026" in status


def test_await_confirmation_times_out_when_never_confirmed():
    client = _FakeHttpClient({"result": {"value": [None]}})
    status = signer._await_confirmation("5Sig", client=client, timeout_s=0.05, poll_interval_s=0.01)
    assert status.startswith("unknown")
