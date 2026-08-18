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


# --- fetch_program_idl (18/08) -----------------------------------------------

_FAKE_IDL_JSON = (
    '{"version":"2.1.0","name":"squads_multisig_program",'
    '"instructions":[{"name":"multisigCreateV2"},{"name":"spendingLimitUse"}]}'
)


async def test_fetch_program_idl_happy_path():
    async def _fake_fetch():
        return _FAKE_IDL_JSON

    result = await ssw.fetch_program_idl(fetch_fn=_fake_fetch)
    assert result["error"] is None
    assert result["idl_name"] == "squads_multisig_program"
    assert result["idl_version"] == "2.1.0"
    assert result["instruction_count"] == 2
    assert result["idl"]["instructions"][0]["name"] == "multisigCreateV2"
    assert result["program_id"] == ssw.SQUADS_V4_PROGRAM_ID


async def test_fetch_program_idl_never_raises_on_network_failure():
    async def _fake_fetch():
        raise ConnectionError("RPC unreachable")

    result = await ssw.fetch_program_idl(fetch_fn=_fake_fetch)
    assert result["error"] is not None
    assert result["idl"] is None


async def test_fetch_program_idl_handles_missing_idl_account():
    async def _fake_fetch():
        return None

    result = await ssw.fetch_program_idl(fetch_fn=_fake_fetch)
    assert result["error"] is None
    assert result["idl"] is None


async def test_fetch_program_idl_flags_invalid_json():
    async def _fake_fetch():
        return "not valid json {{{"

    result = await ssw.fetch_program_idl(fetch_fn=_fake_fetch)
    assert result["error"] is not None
    assert result["idl"] is None
