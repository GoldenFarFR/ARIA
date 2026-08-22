"""Signing and sending a Jupiter swap -- real-money path, offline tests only."""
from __future__ import annotations

import base64
import json
import os

import httpx
import pytest

from aria_core.onchain import jupiter_swap_signer as signer


@pytest.fixture
def key_file(tmp_path):
    from solders.keypair import Keypair

    kp = Keypair()
    p = tmp_path / "delegate.json"
    p.write_text(json.dumps(list(bytes(kp))))
    p.chmod(0o600)
    return str(p), kp


def test_a_key_readable_by_others_is_refused(key_file):
    """A world-readable key on a shared VPS is a real finding, not a style
    preference."""
    path, _ = key_file
    os.chmod(path, 0o644)

    with pytest.raises(signer.DelegateKeyError, match="readable by others"):
        signer.load_keypair(path)


def test_no_key_path_is_refused_rather_than_guessed(key_file):
    with pytest.raises(signer.DelegateKeyError):
        signer.load_keypair("")


def test_a_missing_key_file_raises_a_distinct_error():
    """A missing key is an operator/config problem, not a market one."""
    with pytest.raises(signer.DelegateKeyError, match="not found"):
        signer.load_keypair("/nonexistent/delegate.json")


def test_a_key_error_never_leaks_key_material(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2, 3]")
    p.chmod(0o600)

    with pytest.raises(signer.DelegateKeyError) as exc:
        signer.load_keypair(str(p))
    assert "1, 2, 3" not in str(exc.value)


def test_signing_produces_a_transaction_carrying_a_real_signature(key_file):
    """Round-trip through solders: an unsigned transaction goes in, a signed
    one comes out, and the signature verifies against the message."""
    from solders.hash import Hash
    from solders.instruction import Instruction
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction

    _, kp = key_file
    ix = Instruction(Pubkey.default(), b"", [])
    msg = MessageV0.try_compile(kp.pubkey(), [ix], [], Hash.default())
    unsigned = VersionedTransaction.populate(msg, [])
    raw = base64.b64encode(bytes(unsigned)).decode()

    signed_b64 = signer.sign_transaction(raw, kp)
    signed = VersionedTransaction.from_bytes(base64.b64decode(signed_b64))

    assert len(signed.signatures) == 1
    assert signed.signatures[0].verify(kp.pubkey(), to_bytes_versioned(signed.message))


@pytest.mark.asyncio
async def test_a_failing_simulation_blocks_the_send_entirely(key_file, monkeypatch):
    """Unconditional pre-flight: a swap that fails in simulation would fail
    on-chain, and paying a fee to discover that must not be possible by
    accident. There is no parameter to skip this."""
    path, _ = key_file
    sent = []

    async def _build(_q, _pub, client=None, **_kw):
        return base64.b64encode(b"tx").decode()

    async def _sim(_tx, rpc_http_url=None, client=None):
        return {"ok": False, "error": {"InstructionError": [0, "Custom"]},
                "compute_units": 10, "logs": []}

    async def _rpc(method, params, *, rpc_http_url, client):
        sent.append(method)
        return {"result": "sig"}

    monkeypatch.setattr(signer, "build_swap_transaction", _build)
    monkeypatch.setattr(signer, "simulate_swap_transaction", _sim)
    monkeypatch.setattr(signer, "_rpc", _rpc)

    out = await signer.execute_swap(
        {"outAmount": "10", "slippage_bps_used": 1000}, path,
        rpc_http_url="https://rpc", client=object(),
    )

    assert out["status"] == "failed"
    assert out["reason"] == "simulation_failed"
    assert sent == [], "nothing may be sent once the simulation failed"


@pytest.mark.asyncio
async def test_a_quote_above_the_slippage_ceiling_never_reaches_the_key(key_file):
    path, _ = key_file
    with pytest.raises(signer.SwapSignerError, match="slippage"):
        await signer.execute_swap({"outAmount": "1", "slippage_bps_used": 5000}, path)


@pytest.mark.asyncio
async def test_only_a_finalized_status_is_reported_as_success(key_file, monkeypatch):
    """`confirmed` is not enough: a real race was found on the Squads leg where
    state read right after `confirmed` was still pre-transaction."""
    calls = {"n": 0}

    async def _statuses(method, params, *, rpc_http_url, client):
        calls["n"] += 1
        stage = "confirmed" if calls["n"] == 1 else "finalized"
        return {"result": {"value": [{"err": None, "confirmationStatus": stage}]}}

    monkeypatch.setattr(signer, "_rpc", _statuses)
    monkeypatch.setattr(signer.asyncio, "sleep", lambda _s: asyncio_noop())

    async def asyncio_noop():
        return None

    out = await signer._await_finalized("sig", rpc_http_url="https://rpc", client=object())
    assert out == "ok"
    assert calls["n"] >= 2, "a `confirmed` status must not end the wait"


def test_the_module_holds_no_gate_no_cap_and_no_killswitch():
    """This module DECIDES nothing -- gate, kill-switch and cap live in the
    wrapper production must call. Wiring it directly into a trading loop would
    bypass every guardrail the dome has."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(signer))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("outgoing_pause", "custody_pause", "wallet_guard"):
        assert forbidden not in names, f"{forbidden} belongs to the wrapper, not here"


class TestCommitmentLevel:
    """22/08: `finalized` cost a MEASURED 12-13s per trade on a paid endpoint --
    that is the consensus, not the provider. On the trading path, where no
    on-chain state is read afterwards, it bought a guarantee nothing used while
    the bonding curve moved underneath."""

    @staticmethod
    def _client(status):
        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"result": {"value": [status]}}

        class _Client:
            async def post(self, *a, **k):
                return _Resp()

        return _Client()

    @pytest.mark.asyncio
    async def test_confirmed_accepts_a_confirmed_status(self):
        out = await signer._await_finalized(
            "sig", rpc_http_url="http://rpc",
            client=self._client({"confirmationStatus": "confirmed", "err": None}),
            commitment=signer.COMMITMENT_CONFIRMED,
        )
        assert out == "ok"

    @pytest.mark.asyncio
    async def test_finalized_satisfies_a_confirmed_request(self):
        """A stricter status must never read as 'not there yet'."""
        out = await signer._await_finalized(
            "sig", rpc_http_url="http://rpc",
            client=self._client({"confirmationStatus": "finalized", "err": None}),
            commitment=signer.COMMITMENT_CONFIRMED,
        )
        assert out == "ok"

    @pytest.mark.asyncio
    async def test_a_chain_error_fails_at_either_level(self):
        for level in (signer.COMMITMENT_CONFIRMED, signer.COMMITMENT_FINALIZED):
            out = await signer._await_finalized(
                "sig", rpc_http_url="http://rpc",
                client=self._client({"confirmationStatus": "confirmed", "err": "boom"}),
                commitment=level,
            )
            assert out == "failed"

    @pytest.mark.asyncio
    async def test_an_unknown_level_refuses_rather_than_guessing(self):
        with pytest.raises(signer.SwapSignerError):
            await signer._await_finalized(
                "sig", rpc_http_url="http://rpc",
                client=self._client({"confirmationStatus": "finalized", "err": None}),
                commitment="processed",
            )

    def test_finalized_remains_the_default(self):
        """Callers that read state afterwards must be untouched by this change."""
        import inspect

        params = inspect.signature(signer.execute_swap).parameters
        assert params["commitment"].default == signer.COMMITMENT_FINALIZED

    def test_skipping_confirmation_requires_the_reconciler_to_exist(self):
        """Neither leg waits for a slot, which is ONLY safe while the repair
        path exists. If reconcile_with_chain is ever removed or renamed, this
        fails rather than leaving real trades unverified -- the FOMO stranding
        of 22/08 is precisely what happens without it."""
        from aria_core import solana_agent_wallet as w
        from aria_core import solana_late_bonding_shadow as pocket
        from aria_core.onchain import jupiter_swap_signer as s

        fast = (w.BUY_COMMITMENT == s.COMMITMENT_SENT
                or w.SELL_COMMITMENT == s.COMMITMENT_SENT)
        if fast:
            assert callable(getattr(pocket, "reconcile_with_chain", None)), (
                "trades skip confirmation but nothing reconciles them with the chain"
            )

    @pytest.mark.asyncio
    async def test_sent_returns_without_any_status_call(self):
        """The whole point: no round trip, no slot wait."""

        class _Client:
            async def post(self, *a, **k):
                raise AssertionError("`sent` must not poll for a status")

        out = await signer._await_finalized(
            "sig", rpc_http_url="http://rpc", client=_Client(),
            commitment=signer.COMMITMENT_SENT,
        )
        assert out == "ok"
