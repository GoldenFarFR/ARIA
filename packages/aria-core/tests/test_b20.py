"""B20 native token standard -- detection + issuer-power reader (31/07).
Offline (fake w3 injected), no real network call."""
from __future__ import annotations

import pytest

from aria_core.services import b20

TOKEN = "0xB200000000000000000000289914488470f54529"
GRANTEE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
GRANTEE_2 = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
ROLE_IDS = {
    "MINT_ROLE": "0xe9a9c850000000000000000000000000000000000000000000000000000000",
    "PAUSE_ROLE": "0x389ed267000000000000000000000000000000000000000000000000000000",
    "BURN_BLOCKED_ROLE": "0x32ad9be8000000000000000000000000000000000000000000000000000000",
}


def _log(event: str, role: str, account: str, block: int, index: int) -> dict:
    return {
        "event": event,
        "blockNumber": block,
        "logIndex": index,
        "args": {"role": ROLE_IDS[role], "account": account, "sender": GRANTEE},
    }


class _FakeCall:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _FakeEvent:
    """Simulates ``contract.events.<Name>``. ``windows`` maps
    (from_block, to_block) -> list of log dicts for THIS event type only."""

    def __init__(self, name: str, windows: dict[tuple[int, int], list[dict]], *, raise_on=None):
        self._name = name
        self._windows = windows
        self._raise_on = raise_on or set()

    def get_logs(self, *, from_block, to_block, argument_filters=None):
        if (from_block, to_block) in self._raise_on:
            raise ConnectionError("RPC down")
        return [
            e for e in self._windows.get((from_block, to_block), []) if e["event"] == self._name
        ]


class _FakeEvents:
    def __init__(self, granted_by_window, revoked_by_window=None, *, raise_on=None):
        merged: dict[tuple[int, int], list[dict]] = {}
        for k, v in granted_by_window.items():
            merged.setdefault(k, []).extend(v)
        for k, v in (revoked_by_window or {}).items():
            merged.setdefault(k, []).extend(v)
        self.RoleGranted = _FakeEvent("RoleGranted", merged, raise_on=raise_on)
        self.RoleRevoked = _FakeEvent("RoleRevoked", merged, raise_on=raise_on)


class _FakeFunctions:
    def __init__(self, *, is_b20=True, role_ids=None):
        self._is_b20 = is_b20
        self._role_ids = role_ids or ROLE_IDS

    def isB20(self, address):  # noqa: N802
        return _FakeCall(self._is_b20)

    def isB20Initialized(self, address):  # noqa: N802
        return _FakeCall(self._is_b20)

    def MINT_ROLE(self):  # noqa: N802
        return _FakeCall(self._role_ids["MINT_ROLE"])

    def PAUSE_ROLE(self):  # noqa: N802
        return _FakeCall(self._role_ids["PAUSE_ROLE"])

    def BURN_BLOCKED_ROLE(self):  # noqa: N802
        return _FakeCall(self._role_ids["BURN_BLOCKED_ROLE"])


class _FakeContract:
    def __init__(self, address, functions, events=None):
        self.address = address
        self.functions = functions
        self.events = events


class _FakeEth:
    def __init__(self, *, is_b20=True, block_number=1000, windows=None, raise_on_logs=None, break_contract=False):
        self._is_b20 = is_b20
        self.block_number = block_number
        self._windows = windows or {}
        self._raise_on_logs = raise_on_logs
        self._break_contract = break_contract

    def contract(self, address, abi):
        if self._break_contract:
            raise ConnectionError("RPC down")
        return _FakeContract(
            address,
            _FakeFunctions(is_b20=self._is_b20),
            _FakeEvents(self._windows, raise_on=self._raise_on_logs),
        )


class _FakeW3:
    def __init__(self, **kwargs):
        self.eth = _FakeEth(**kwargs)

    def to_checksum_address(self, addr):
        return addr


@pytest.mark.asyncio
async def test_is_b20_true():
    w3 = _FakeW3(is_b20=True)
    assert await b20.is_b20(TOKEN, w3=w3) is True


@pytest.mark.asyncio
async def test_is_b20_false_for_lookalike():
    """31/07 real finding: GREEN/ADS both started with '0xb20' but the
    factory's own isB20() correctly said False -- confirms this module
    trusts the factory, never the address text."""
    w3 = _FakeW3(is_b20=False)
    assert await b20.is_b20(TOKEN, w3=w3) is False


@pytest.mark.asyncio
async def test_is_b20_none_on_missing_address():
    assert await b20.is_b20("", w3=_FakeW3()) is None


@pytest.mark.asyncio
async def test_is_b20_none_on_rpc_failure():
    w3 = _FakeW3(break_contract=True)
    assert await b20.is_b20(TOKEN, w3=w3) is None


@pytest.mark.asyncio
async def test_scan_role_holders_empty_history_is_complete_renounced():
    """No grants ever -- scan walks all the way to block 0 (small
    block_number here keeps the test fast) and confirms complete+empty."""
    w3 = _FakeW3(block_number=10, windows={})
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert result.complete is True
    assert result.holders == set()


@pytest.mark.asyncio
async def test_scan_role_holders_finds_active_grant():
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    windows = {(from_block, to_block): [_log("RoleGranted", "MINT_ROLE", GRANTEE, 950, 0)]}
    w3 = _FakeW3(block_number=to_block, windows=windows)
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert GRANTEE in result.holders


@pytest.mark.asyncio
async def test_scan_role_holders_revoke_after_grant_nets_to_empty():
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    windows = {
        (from_block, to_block): [
            _log("RoleGranted", "MINT_ROLE", GRANTEE, 950, 0),
            _log("RoleRevoked", "MINT_ROLE", GRANTEE, 970, 0),
        ]
    }
    w3 = _FakeW3(block_number=to_block, windows=windows)
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert GRANTEE not in result.holders


@pytest.mark.asyncio
async def test_scan_role_holders_events_replayed_in_block_order_not_dict_order():
    """A revoke logged BEFORE its grant in the raw list (out of order) must
    still net correctly once sorted by (blockNumber, logIndex)."""
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    windows = {
        (from_block, to_block): [
            _log("RoleRevoked", "MINT_ROLE", GRANTEE, 950, 5),  # later block, listed first
            _log("RoleGranted", "MINT_ROLE", GRANTEE, 900, 0),  # earlier block, listed second
        ]
    }
    w3 = _FakeW3(block_number=to_block, windows=windows)
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert GRANTEE not in result.holders  # granted then revoked -> net absent


@pytest.mark.asyncio
async def test_scan_role_holders_two_holders_independent():
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    windows = {
        (from_block, to_block): [
            _log("RoleGranted", "MINT_ROLE", GRANTEE, 950, 0),
            _log("RoleGranted", "MINT_ROLE", GRANTEE_2, 960, 0),
            _log("RoleRevoked", "MINT_ROLE", GRANTEE, 970, 0),
        ]
    }
    w3 = _FakeW3(block_number=to_block, windows=windows)
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert GRANTEE not in result.holders
    assert GRANTEE_2 in result.holders


@pytest.mark.asyncio
async def test_scan_role_holders_incomplete_when_horizon_exceeded():
    """A grant sitting exactly at the scan horizon, with non-empty windows
    all the way, never lets the early-stop heuristic or the block-0 exit
    fire within MAX_LOG_SCAN_WINDOWS -- degrades to complete=False."""
    to_block = b20.MAX_LOG_SCAN_WINDOWS * b20.LOG_SCAN_WINDOW_BLOCKS + 10_000_000
    windows = {}
    cursor = to_block
    for _ in range(b20.MAX_LOG_SCAN_WINDOWS):
        from_block = max(0, cursor - b20.LOG_SCAN_WINDOW_BLOCKS + 1)
        # a lone grant in every window keeps consecutive_empty at 0, so the
        # early-stop heuristic never triggers and block 0 is never reached.
        windows[(from_block, cursor)] = [_log("RoleGranted", "MINT_ROLE", GRANTEE, from_block, 0)]
        cursor = from_block - 1
    w3 = _FakeW3(block_number=to_block, windows=windows)
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert result.complete is False


@pytest.mark.asyncio
async def test_scan_role_holders_none_on_role_constant_failure():
    w3 = _FakeW3(break_contract=True)
    assert await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3) is None


@pytest.mark.asyncio
async def test_scan_role_holders_none_on_invalid_role_name():
    assert await b20.scan_role_holders(TOKEN, "NOT_A_REAL_ROLE", w3=_FakeW3()) is None


@pytest.mark.asyncio
async def test_scan_role_holders_get_logs_failure_returns_incomplete_not_none():
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    w3 = _FakeW3(block_number=to_block, windows={}, raise_on_logs={(from_block, to_block)})
    result = await b20.scan_role_holders(TOKEN, "MINT_ROLE", w3=w3)
    assert result is not None
    assert result.complete is False


@pytest.mark.asyncio
async def test_evaluate_b20_safety_not_b20():
    w3 = _FakeW3(is_b20=False)
    verdict = await b20.evaluate_b20_safety(TOKEN, w3=w3)
    assert verdict.verdict == "not_b20"


@pytest.mark.asyncio
async def test_evaluate_b20_safety_opaque_on_unresolved_is_b20():
    w3 = _FakeW3(break_contract=True)
    verdict = await b20.evaluate_b20_safety(TOKEN, w3=w3)
    assert verdict.verdict == "opaque"


@pytest.mark.asyncio
async def test_evaluate_b20_safety_safe_when_all_roles_renounced():
    w3 = _FakeW3(is_b20=True, block_number=10, windows={})
    verdict = await b20.evaluate_b20_safety(TOKEN, w3=w3)
    assert verdict.verdict == "safe"
    assert all(not holders for holders in verdict.role_holders.values())


@pytest.mark.asyncio
async def test_evaluate_b20_safety_risky_when_mint_role_active():
    to_block = 999
    from_block = to_block - b20.LOG_SCAN_WINDOW_BLOCKS + 1
    # Every window empty except one grant on MINT_ROLE at block 0 boundary
    # (fast path: from_block reaches 0 quickly given block_number=999).
    windows = {(from_block, to_block): [_log("RoleGranted", "MINT_ROLE", GRANTEE, 950, 0)]}
    w3 = _FakeW3(is_b20=True, block_number=to_block, windows=windows)
    verdict = await b20.evaluate_b20_safety(TOKEN, w3=w3)
    assert verdict.verdict == "risky"
    assert GRANTEE in verdict.role_holders["MINT_ROLE"]
    assert "MINT_ROLE" in verdict.reason


@pytest.mark.asyncio
async def test_evaluate_b20_safety_opaque_when_scan_incomplete():
    to_block = b20.MAX_LOG_SCAN_WINDOWS * b20.LOG_SCAN_WINDOW_BLOCKS + 10_000_000
    windows = {}
    cursor = to_block
    for _ in range(b20.MAX_LOG_SCAN_WINDOWS):
        from_block = max(0, cursor - b20.LOG_SCAN_WINDOW_BLOCKS + 1)
        windows[(from_block, cursor)] = [_log("RoleGranted", "MINT_ROLE", GRANTEE, from_block, 0)]
        cursor = from_block - 1
    w3 = _FakeW3(is_b20=True, block_number=to_block, windows=windows)
    verdict = await b20.evaluate_b20_safety(TOKEN, w3=w3)
    assert verdict.verdict == "opaque"
    assert "MINT_ROLE" in verdict.reason


def test_rpc_url_default(monkeypatch):
    monkeypatch.delenv("ARIA_BASE_RPC_URL", raising=False)
    assert b20._rpc_url() == "https://mainnet.base.org"


def test_rpc_url_override(monkeypatch):
    monkeypatch.setenv("ARIA_BASE_RPC_URL", "https://custom.example/rpc")
    assert b20._rpc_url() == "https://custom.example/rpc"
