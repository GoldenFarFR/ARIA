"""17/08, first milestone of the homemade agent wallet's Robinhood Chain leg
-- read-only contract-deployment verification only, no signing yet. Mirrors
test_sepolia_wallet.py's fake-w3 injection pattern (never a real network
call in tests)."""
from __future__ import annotations

from aria_core.onchain import safe_robinhood_wallet as srw


class _FakeEth:
    def __init__(self, *, chain_id, code_by_address):
        self.chain_id = chain_id
        self._code_by_address = code_by_address

    def get_code(self, address):
        return self._code_by_address[address]


class _FakeW3:
    def __init__(self, *, chain_id, code_by_address):
        self.eth = _FakeEth(chain_id=chain_id, code_by_address=code_by_address)


def _deployed_w3(chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID):
    return _FakeW3(
        chain_id=chain_id,
        code_by_address={
            srw.SAFE_SINGLETON_V141_ADDRESS: b"\x60\x80" * 100,
            srw.ALLOWANCE_MODULE_ADDRESS: b"\x60\x80" * 50,
        },
    )


def test_verify_contracts_deployed_happy_path():
    result = srw.verify_contracts_deployed(w3=_deployed_w3())
    assert result["error"] is None
    assert result["chain_id_ok"] is True
    assert result["safe_singleton"]["deployed"] is True
    assert result["allowance_module"]["deployed"] is True


def test_verify_contracts_deployed_flags_wrong_chain():
    result = srw.verify_contracts_deployed(w3=_deployed_w3(chain_id=1))
    assert result["chain_id_ok"] is False
    assert result["chain_id"] == 1


def test_verify_contracts_deployed_flags_empty_bytecode():
    w3 = _FakeW3(
        chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID,
        code_by_address={srw.SAFE_SINGLETON_V141_ADDRESS: b"", srw.ALLOWANCE_MODULE_ADDRESS: b""},
    )
    result = srw.verify_contracts_deployed(w3=w3)
    assert result["safe_singleton"]["deployed"] is False
    assert result["allowance_module"]["deployed"] is False


def test_verify_contracts_deployed_never_raises_on_rpc_failure():
    class _BrokenEth:
        @property
        def chain_id(self):
            raise ConnectionError("RPC unreachable")

    class _BrokenW3:
        eth = _BrokenEth()

    result = srw.verify_contracts_deployed(w3=_BrokenW3())
    assert result["error"] is not None
    assert result["chain_id_ok"] is None


# ── Step 1 (17/08): read-only AllowanceModule wiring ──────────────────────
# Fake contract mimicking web3's `contract.functions.NAME().call()` shape,
# so no test ever touches the real network.

ADDR = "0x" + "11" * 20  # any checksummable placeholder
SAFE = "0x" + "22" * 20
DELEGATE = "0x" + "33" * 20
TOKEN = "0x" + "44" * 20


class _FakeCall:
    def __init__(self, value):
        self._value = value

    def call(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class _FakeFunctions:
    def __init__(self, returns):
        self._returns = returns

    def __getattr__(self, name):
        def _fn(*args):
            value = self._returns[name]
            return _FakeCall(value(*args) if callable(value) else value)

        return _fn


class _FakeContract:
    def __init__(self, returns):
        self.functions = _FakeFunctions(returns)


class _ContractW3:
    """Minimal w3 stand-in whose `.eth.contract()` returns our fake."""

    def __init__(self, returns, chain_id=srw.ROBINHOOD_TESTNET_CHAIN_ID):
        outer = self

        class _Eth:
            def __init__(self):
                if chain_id is not None:
                    self.chain_id = chain_id

            def contract(self, address=None, abi=None):
                outer.seen_abi = abi
                return _FakeContract(returns)

        self.eth = _Eth()
        self.seen_abi = None


def test_allowance_module_abi_is_read_only():
    """Structural guardrail (not a convention): the embedded ABI must never
    contain a state-changing function, so web3 physically cannot build a
    spend call from this module. Breaking this must be deliberate."""
    for entry in srw._ALLOWANCE_MODULE_VIEW_ABI:
        assert entry["stateMutability"] in ("view", "pure"), entry["name"]
    names = {e["name"] for e in srw._ALLOWANCE_MODULE_VIEW_ABI}
    for forbidden in ("setAllowance", "executeAllowanceTransfer", "addDelegate",
                      "removeDelegate", "deleteAllowance", "resetAllowance"):
        assert forbidden not in names


def test_read_module_identity_matches_expected():
    w3 = _ContractW3({"NAME": "Allowance Module", "VERSION": "0.1.1"})
    result = srw.read_module_identity(w3=w3)
    assert result["error"] is None
    assert result["matches_expected"] is True


def test_read_module_identity_flags_a_different_contract_at_same_address():
    """The case verify_contracts_deployed cannot catch: real bytecode is
    present, but it is not the contract we think it is."""
    w3 = _ContractW3({"NAME": "Something Else", "VERSION": "9.9.9"})
    result = srw.read_module_identity(w3=w3)
    assert result["error"] is None
    assert result["matches_expected"] is False


def test_read_module_identity_never_raises_on_failure():
    w3 = _ContractW3({"NAME": ConnectionError("RPC down")})
    result = srw.read_module_identity(w3=w3)
    assert result["error"] is not None
    assert result["name"] is None


def test_read_module_identity_reports_the_real_chain_id():
    ok = srw.read_module_identity(w3=_ContractW3({"NAME": "Allowance Module", "VERSION": "0.1.1"}))
    assert ok["chain_id"] == srw.ROBINHOOD_TESTNET_CHAIN_ID
    assert ok["on_expected_testnet"] is True


def test_read_module_identity_flags_reading_the_wrong_chain():
    """The residual this check exists for: a misconfigured RPC pointing at
    mainnet (4663) instead of the testnet must be visible, not silent."""
    w3 = _ContractW3({"NAME": "Allowance Module", "VERSION": "0.1.1"}, chain_id=4663)
    result = srw.read_module_identity(w3=w3)
    assert result["chain_id"] == 4663
    assert result["on_expected_testnet"] is False
    assert result["matches_expected"] is True  # right contract, wrong network


def test_read_module_identity_survives_an_unreadable_chain_id():
    """A chain-id read failure must never turn a successful identity check
    into a failure -- degraded, not broken."""
    w3 = _ContractW3({"NAME": "Allowance Module", "VERSION": "0.1.1"}, chain_id=None)
    result = srw.read_module_identity(w3=w3)
    assert result["error"] is None
    assert result["matches_expected"] is True
    assert result["chain_id"] is None
    assert result["on_expected_testnet"] is None


def test_read_allowance_decodes_fields_and_remaining():
    # [amount, spent, resetTimeMin, lastResetMin, nonce]
    w3 = _ContractW3({"getTokenAllowance": [1_000_000, 250_000, 1440, 29_000_000, 3]})
    result = srw.read_allowance(SAFE, DELEGATE, TOKEN, w3=w3)
    assert result["error"] is None
    assert result["amount"] == 1_000_000
    assert result["spent"] == 250_000
    assert result["remaining"] == 750_000
    assert result["reset_time_min"] == 1440
    assert result["nonce"] == 3
    assert result["configured"] is True


def test_read_allowance_distinguishes_never_configured_from_fully_spent():
    """Opposite operator actions -- must never be conflated."""
    never_set = srw.read_allowance(
        SAFE, DELEGATE, TOKEN, w3=_ContractW3({"getTokenAllowance": [0, 0, 0, 0, 0]})
    )
    assert never_set["configured"] is False
    assert never_set["remaining"] == 0

    exhausted = srw.read_allowance(
        SAFE, DELEGATE, TOKEN, w3=_ContractW3({"getTokenAllowance": [500, 500, 1440, 29_000_000, 7]})
    )
    assert exhausted["configured"] is True
    assert exhausted["remaining"] == 0


def test_read_allowance_distinguishes_periodic_from_one_shot():
    """`reset_time_min == 0` is a one-shot allowance that never refills --
    materially different from a daily cap, and reachable on v0.1.1."""
    periodic = srw.read_allowance(
        SAFE, DELEGATE, TOKEN, w3=_ContractW3({"getTokenAllowance": [1000, 0, 1440, 29_000_000, 1]})
    )
    assert periodic["renews"] is True

    one_shot = srw.read_allowance(
        SAFE, DELEGATE, TOKEN, w3=_ContractW3({"getTokenAllowance": [1000, 0, 0, 29_000_000, 1]})
    )
    assert one_shot["renews"] is False
    assert one_shot["configured"] is True  # still a real, funded allowance


def test_read_allowance_never_raises_on_failure():
    w3 = _ContractW3({"getTokenAllowance": ConnectionError("RPC down")})
    result = srw.read_allowance(SAFE, DELEGATE, TOKEN, w3=w3)
    assert result["error"] is not None
    assert result["remaining"] is None


def test_read_delegates_walks_pagination_to_the_end():
    pages = {0: ([ADDR], 5), 5: ([SAFE, TOKEN], 0)}
    w3 = _ContractW3({"getDelegates": lambda safe, start, size: pages[start]})
    result = srw.read_delegates(SAFE, w3=w3)
    assert result["error"] is None
    assert result["delegates"] == [ADDR, SAFE, TOKEN]
    assert result["truncated"] is False


def test_read_delegates_flags_truncation_instead_of_silently_partial():
    """A partial delegate list presented as complete would badly mislead a
    security review -- it must be flagged, never silently cut."""
    w3 = _ContractW3({"getDelegates": lambda safe, start, size: ([ADDR], start + 1)})
    result = srw.read_delegates(SAFE, w3=w3)
    assert result["error"] is None
    assert result["truncated"] is True
    assert len(result["delegates"]) == srw._MAX_DELEGATE_PAGES


def test_read_delegates_never_raises_on_failure():
    w3 = _ContractW3({"getDelegates": ConnectionError("RPC down")})
    result = srw.read_delegates(SAFE, w3=w3)
    assert result["error"] is not None
    assert result["delegates"] is None


def test_read_allowance_tokens_happy_path_and_failure():
    ok = srw.read_allowance_tokens(SAFE, DELEGATE, w3=_ContractW3({"getTokens": [TOKEN]}))
    assert ok["error"] is None
    assert ok["tokens"] == [TOKEN]

    ko = srw.read_allowance_tokens(
        SAFE, DELEGATE, w3=_ContractW3({"getTokens": ConnectionError("RPC down")})
    )
    assert ko["error"] is not None
    assert ko["tokens"] is None
