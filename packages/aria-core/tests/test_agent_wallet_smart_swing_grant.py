"""Tests for the one-time Spend Permission grant on aria-smart-st via the Tangem
owner (``agent_wallet_smart_swing_grant``).

NEVER a live network call and NEVER a real Tangem tap: every external touchpoint
(the CDP/web3 on-chain reads, the CDP submit, and the tangem_bridge client) is an
injected fake, exactly the pattern the sibling adapter tests use. These verify
the call SEQUENCE and the exact PAYLOAD construction (the memo's hard
requirements: frozen permission threaded identically everywhere, the exact
replay-safe typed data sent to eth_signTypedData_v4, v-normalization, the
ERC-6492 branch, and that success is claimed ONLY on a confirmed isApproved())."""
from __future__ import annotations

import inspect
import json

import pytest

from aria_core import agent_wallet_smart_swing_grant as grant
from aria_core.agent_wallet_smart_swing import (
    SMART_ST_ADDRESS,
    SPENDER_ADDRESS,
    SPEND_PERMISSION_MANAGER_ADDRESS,
    TANGEM_ST_OWNER,
)
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

# approveWithSignature(SpendPermission,bytes) selector (verified offline against
# the installed SPEND_PERMISSION_MANAGER_ABI).
_APPROVE_SELECTOR = "0xb9ffc8e1"

# A canonical 65-byte signature with v=0 (to exercise normalization to 27).
_SIG_V0 = "0x" + "11" * 32 + "22" * 32 + "00"
_INNER_HASH = "0x" + "ab" * 32


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeBridge:
    """Stands in for aria_core.tangem_bridge. Records every call so the sequence
    and payloads can be asserted; never touches a network or a card."""

    def __init__(self, *, connected=TANGEM_ST_OWNER, sig=_SIG_V0, reject=False,
                 start_available=True, connect_status="connected"):
        self.connected = connected
        self.sig = sig
        self.reject = reject
        self.start_available = start_available
        self.connect_status = connect_status
        self.calls: list = []

    async def start_connection(self, *, timeout_seconds):
        self.calls.append(("start", timeout_seconds))
        return type("C", (), {
            "available": self.start_available, "connection_id": "c1" if self.start_available else None,
            "uri": "wc:deadbeef@2", "error": None if self.start_available else "unreachable",
        })()

    async def wait_for_connection(self, cid, *, timeout_seconds):
        self.calls.append(("wait", cid))
        return type("S", (), {
            "available": True, "status": self.connect_status, "address": self.connected, "error": None,
        })()

    async def request_signature(self, cid, method, params, *, chain_id, timeout_seconds):
        self.calls.append(("sign", method, params, chain_id))
        if self.reject:
            return type("R", (), {"available": False, "result": None, "error": "user declined"})()
        return type("R", (), {"available": True, "result": self.sig, "error": None})()

    async def disconnect(self, cid, *, timeout_seconds=10.0):
        self.calls.append(("disc", cid))
        return True


def _fakes(*, deployed=True, approved=True, hash_raises=False, code_raises=False,
           submit_raises=False):
    """Build the four injectable seams + a shared capture dict."""
    cap: dict = {"get_hash_tuples": [], "is_approved_tuples": [], "submit": None}

    async def get_hash_fn(pt):
        cap["get_hash_tuples"].append(pt)
        if hash_raises:
            raise RuntimeError("rpc down")
        return _INNER_HASH

    async def get_code_fn(addr):
        cap["get_code_addr"] = addr
        if code_raises:
            raise RuntimeError("rpc down")
        return b"\x60\x60" if deployed else b""

    async def is_approved_fn(pt):
        cap["is_approved_tuples"].append(pt)
        return approved

    async def submit_fn(*, to, data, network):
        if submit_raises:
            raise RuntimeError("policy denied / broadcast failed")
        cap["submit"] = {"to": to, "data": data, "network": network}
        return "0xTXHASH"

    return cap, get_hash_fn, get_code_fn, is_approved_fn, submit_fn


async def _run(**over):
    """Run the orchestrator with all seams faked and fast confirm polling."""
    fakes_kwargs = {k: over.pop(k) for k in list(over) if k in
                    ("deployed", "approved", "hash_raises", "code_raises", "submit_raises")}
    cap, gh, gc, ia, sub = _fakes(**fakes_kwargs)
    bridge = over.pop("bridge", None) or FakeBridge()
    res = await grant.grant_spend_permission_via_tangem(
        rpc_url="http://dummy", get_hash_fn=gh, get_code_fn=gc,
        is_approved_fn=ia, submit_fn=sub, bridge=bridge,
        confirm_poll_attempts=over.pop("confirm_poll_attempts", 1),
        confirm_poll_interval_seconds=0, **over,
    )
    return res, cap, bridge


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_frozen_permission_carries_the_reviewed_constants():
    perm = grant.build_frozen_permission()
    assert perm.account == SMART_ST_ADDRESS
    assert perm.spender == SPENDER_ADDRESS
    assert perm.token.lower() == USDC_BASE_ADDRESS.lower()
    assert perm.allowance == 2_500_000_000  # 2500 USDC, 6 decimals
    assert perm.period == 7 * 24 * 60 * 60
    assert isinstance(perm.salt, int) and perm.salt > 0


def test_frozen_permission_uses_a_fresh_random_salt_each_call():
    # Exactly why the orchestrator must resolve ONCE and thread the frozen struct:
    # two resolutions differ, so re-resolving mid-flow would break isApproved.
    assert grant.build_frozen_permission().salt != grant.build_frozen_permission().salt


def test_permission_tuple_order_and_types():
    perm = grant.build_frozen_permission()
    t = grant.build_permission_tuple(perm)
    assert t[0] == SMART_ST_ADDRESS  # account
    assert t[1] == SPENDER_ADDRESS   # spender
    assert t[2].lower() == USDC_BASE_ADDRESS.lower()  # token
    assert t[3] == int(perm.allowance)
    assert t[4] == int(perm.period)
    assert t[5] == int(perm.start)
    assert t[6] == int(perm.end)
    assert t[7] == int(perm.salt)
    assert t[8] == b""  # extraData "0x" -> empty bytes


def test_replay_safe_typed_data_structure():
    td = grant.build_replay_safe_typed_data(_INNER_HASH, 8453, SMART_ST_ADDRESS)
    assert td["primaryType"] == "CoinbaseSmartWalletMessage"
    assert td["message"] == {"hash": _INNER_HASH}
    assert td["domain"]["name"] == "Coinbase Smart Wallet"
    assert td["domain"]["version"] == "1"
    assert td["domain"]["chainId"] == 8453
    assert td["domain"]["verifyingContract"] == SMART_ST_ADDRESS
    assert td["types"]["CoinbaseSmartWalletMessage"] == [{"name": "hash", "type": "bytes32"}]


def test_replay_safe_typed_data_prefixes_hash():
    td = grant.build_replay_safe_typed_data("ab" * 32, 8453, SMART_ST_ADDRESS)
    assert td["message"]["hash"] == "0x" + "ab" * 32


@pytest.mark.parametrize("v_in,v_out", [("00", "1b"), ("01", "1c"), ("1b", "1b"), ("1c", "1c")])
def test_normalize_signature_v(v_in, v_out):
    sig = "0x" + "11" * 32 + "22" * 32 + v_in
    assert grant.normalize_signature_v(sig).endswith(v_out)


def test_normalize_signature_v_leaves_noncanonical_untouched():
    assert grant.normalize_signature_v("0xdeadbeef") == "0xdeadbeef"


def test_wrap_owner_signature_deployed_is_not_6492_and_normalizes_v():
    from eth_abi import decode

    normalized = grant.normalize_signature_v(_SIG_V0)  # v 00 -> 1b
    wrapped, used_6492 = grant.wrap_owner_signature(
        normalized, account_deployed=True, owner_address=TANGEM_ST_OWNER
    )
    assert used_6492 is False
    # Decode the SignatureWrapper(uint8 ownerIndex, bytes signatureData).
    owner_index, sig_data = decode(["uint8", "bytes"], bytes.fromhex(wrapped.removeprefix("0x")))
    assert owner_index == 0
    assert sig_data[-1] == 0x1B  # v normalized into the wrapped signature


def test_wrap_owner_signature_undeployed_appends_6492_magic():
    normalized = grant.normalize_signature_v(_SIG_V0)
    wrapped, used_6492 = grant.wrap_owner_signature(
        normalized, account_deployed=False, owner_address=TANGEM_ST_OWNER
    )
    assert used_6492 is True
    assert wrapped.endswith("6492" * 16)  # ERC-6492 magic suffix


def test_encode_approve_with_signature_selector():
    perm = grant.build_frozen_permission()
    t = grant.build_permission_tuple(perm)
    data = grant.encode_approve_with_signature_calldata(t, "0x" + "00" * 97)
    assert data.startswith(_APPROVE_SELECTOR)


# ── Orchestrator: dry-run (default, no tap, no submit) ───────────────────────


async def test_dry_run_is_the_default_and_touches_no_card_or_chain_write():
    cap, gh, gc, ia, sub = _fakes()
    bridge = FakeBridge()
    res = await grant.grant_spend_permission_via_tangem(
        rpc_url="http://dummy", get_hash_fn=gh, get_code_fn=gc,
        is_approved_fn=ia, submit_fn=sub, bridge=bridge,
    )
    assert res.status == grant.STATUS_DRY_RUN
    assert res.granted is False
    assert res.replay_safe_typed_data["message"]["hash"] == _INNER_HASH
    assert res.account_deployed is True
    # Read-only chain reads happened; NO bridge interaction, NO submit.
    assert cap["get_hash_tuples"] and "get_code_addr" in cap
    assert bridge.calls == []
    assert cap["submit"] is None


async def test_dry_run_echoes_only_the_reviewed_envelope():
    res, _, _ = await _run(dry_run=True)
    assert res.account == SMART_ST_ADDRESS
    assert res.spender == SPENDER_ADDRESS
    assert res.token == USDC_BASE_ADDRESS
    assert res.allowance_atomic == 2_500_000_000


# ── Orchestrator: real grant path ────────────────────────────────────────────


async def test_real_deployed_grant_confirmed_sequence_and_payload():
    res, cap, bridge = await _run(dry_run=False, deployed=True, approved=True)
    assert res.status == grant.STATUS_GRANTED
    assert res.granted is True
    assert res.used_erc6492 is False
    assert res.tx_hash == "0xTXHASH"

    # Bridge call sequence: connect -> wait -> sign -> disconnect.
    assert [c[0] for c in bridge.calls] == ["start", "wait", "sign", "disc"]
    sign = next(c for c in bridge.calls if c[0] == "sign")
    assert sign[1] == "eth_signTypedData_v4"
    assert sign[2][0] == TANGEM_ST_OWNER
    assert json.loads(sign[2][1])["message"]["hash"] == _INNER_HASH  # exact payload signed
    assert sign[3] == "eip155:8453"

    # Submitted to the manager, approveWithSignature calldata, from the spender network.
    assert cap["submit"]["to"] == SPEND_PERMISSION_MANAGER_ADDRESS
    assert cap["submit"]["data"].startswith(_APPROVE_SELECTOR)
    assert cap["submit"]["network"] == "base"


async def test_real_undeployed_uses_erc6492():
    res, _, _ = await _run(dry_run=False, deployed=False, approved=True)
    assert res.status == grant.STATUS_GRANTED
    assert res.used_erc6492 is True


async def test_frozen_permission_is_threaded_identically_everywhere():
    # The SAME struct must reach getHash, isApproved, and the approveWithSignature
    # calldata -- a re-resolve (fresh salt) would silently break the grant.
    from web3 import Web3

    from cdp.spend_permissions import SPEND_PERMISSION_MANAGER_ABI

    res, cap, _ = await _run(dry_run=False, deployed=True, approved=True)
    assert res.status == grant.STATUS_GRANTED
    hashed = cap["get_hash_tuples"][0]
    approved_with = cap["is_approved_tuples"][0]
    assert hashed == approved_with  # getHash and isApproved saw the identical tuple

    # Decode the submitted calldata: the embedded SpendPermission must be that
    # same frozen struct (same salt above all).
    w3 = Web3()
    c = w3.eth.contract(
        address=w3.to_checksum_address(SPEND_PERMISSION_MANAGER_ADDRESS),
        abi=SPEND_PERMISSION_MANAGER_ABI,
    )
    fn, args = c.decode_function_input(cap["submit"]["data"])
    assert fn.fn_name == "approveWithSignature"
    embedded = args["spendPermission"]
    assert int(embedded["salt"]) == hashed[7]
    assert int(embedded["allowance"]) == hashed[3]
    assert Web3.to_checksum_address(embedded["spender"]) == hashed[1]


async def test_success_requires_confirmed_isapproved():
    res, cap, _ = await _run(dry_run=False, deployed=True, approved=False, confirm_poll_attempts=2)
    assert res.status == grant.STATUS_SUBMITTED_NOT_CONFIRMED
    assert res.granted is False
    assert res.tx_hash == "0xTXHASH"  # tx WAS sent...
    assert "isApproved" in res.reason  # ...but never claimed granted


# ── Orchestrator: refusal / failure paths (never a false grant) ──────────────


async def test_operator_decline_is_rejected_and_never_submits():
    res, cap, bridge = await _run(dry_run=False, bridge=FakeBridge(reject=True))
    assert res.status == grant.STATUS_REJECTED
    assert res.granted is False
    assert cap["submit"] is None
    assert ("disc", "c1") in bridge.calls  # session still cleaned up


async def test_bridge_unreachable_at_pairing_is_error_no_submit():
    res, cap, _ = await _run(dry_run=False, bridge=FakeBridge(start_available=False))
    assert res.status == grant.STATUS_ERROR
    assert cap["submit"] is None


async def test_connection_not_confirmed_is_error_no_submit():
    res, cap, bridge = await _run(dry_run=False, bridge=FakeBridge(connect_status="pending"))
    assert res.status == grant.STATUS_ERROR
    assert cap["submit"] is None
    assert ("disc", "c1") in bridge.calls


async def test_wrong_card_owner_is_rejected_before_signing():
    wrong = "0x0000000000000000000000000000000000000001"
    res, cap, bridge = await _run(dry_run=False, bridge=FakeBridge(connected=wrong))
    assert res.status == grant.STATUS_ERROR
    assert cap["submit"] is None
    # No signature was ever requested from the wrong card.
    assert not any(c[0] == "sign" for c in bridge.calls)


async def test_submit_failure_is_error_with_policy_ordering_hint():
    res, _, _ = await _run(dry_run=False, submit_raises=True)
    assert res.status == grant.STATUS_ERROR
    assert res.granted is False
    assert "Policy" in res.reason  # points at the swap-only Policy ordering trap


async def test_gethash_read_failure_aborts_before_any_tap():
    res, cap, bridge = await _run(dry_run=False, hash_raises=True)
    assert res.status == grant.STATUS_ERROR
    assert bridge.calls == []  # never even paired
    assert cap["submit"] is None


async def test_getcode_read_failure_aborts_before_any_tap():
    res, cap, bridge = await _run(dry_run=False, code_raises=True)
    assert res.status == grant.STATUS_ERROR
    assert bridge.calls == []
    assert cap["submit"] is None


# ── Guardrail: what is granted is NOT a free parameter ───────────────────────


def test_no_free_parameter_can_change_the_granted_amount_spender_or_token():
    params = set(inspect.signature(grant.grant_spend_permission_via_tangem).parameters)
    for forbidden in ("allowance", "allowance_usd", "spender", "token", "account", "amount"):
        assert forbidden not in params, (
            f"{forbidden} must never be a parameter -- the granted envelope comes ONLY "
            "from build_spend_permission_input()"
        )
