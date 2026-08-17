"""17/08, first milestone of the homemade agent wallet's Solana leg --
read-only program-deployment verification only, no signing yet. Mirrors
test_safe_robinhood_wallet.py's fake-client injection pattern (never a real
network call in tests)."""
from __future__ import annotations

from aria_core.onchain import squads_solana_wallet as ssw


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload=None, *, raises=None):
        self._payload = payload
        self._raises = raises

    def post(self, url, json, timeout):
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._payload)


def test_verify_program_deployed_happy_path():
    client = _FakeClient({
        "result": {"value": {"executable": True, "owner": "BPFLoaderUpgradeab1e11111111111111111111111"}},
    })
    result = ssw.verify_program_deployed(client=client)
    assert result["error"] is None
    assert result["deployed"] is True
    assert result["executable"] is True
    assert result["program_id"] == ssw.SQUADS_V4_PROGRAM_ID


def test_verify_program_deployed_flags_missing_account():
    client = _FakeClient({"result": {"value": None}})
    result = ssw.verify_program_deployed(client=client)
    assert result["error"] is None
    assert result["deployed"] is False


def test_verify_program_deployed_never_raises_on_rpc_failure():
    client = _FakeClient(raises=ConnectionError("RPC unreachable"))
    result = ssw.verify_program_deployed(client=client)
    assert result["error"] is not None
    assert result["deployed"] is None
