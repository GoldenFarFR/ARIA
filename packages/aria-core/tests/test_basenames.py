"""Basenames forward resolution (07/24) -- no real RPC call, w3 injected.

Namehash validated against the well-known EIP-137 test vector; the 2-step
Registry->Resolver lookup validated end-to-end against a real name
("jesse.base.eth", independently confirmed via Blockscout's ens_domain_name
field) before this module was written -- see basenames.py's docstring for
the exact addresses involved."""
from __future__ import annotations

from aria_core.services import basenames as bn

RESOLVER = "0xC6d566A56A1aFf6508b41f6c90ff131615583BCD"
RESOLVED_ADDR = "0x2211d1D0020DAEA8039E46Cf1367962070d77DA9"


class _FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class _FakeRegistryFunctions:
    def __init__(self, resolver_address):
        self._resolver_address = resolver_address

    def resolver(self, node):
        return _FakeCall(self._resolver_address)


class _FakeResolverFunctions:
    def __init__(self, addr):
        self._addr = addr

    def addr(self, node):
        return _FakeCall(self._addr)


class _FakeContract:
    def __init__(self, functions):
        self.functions = functions


class _FakeEth:
    """Routes `contract(address, abi)` by inspecting the ABI's function name
    (never compares addresses -- keeps the fake decoupled from real hex
    values, which the real module must still use correctly)."""

    def __init__(self, resolver_address=None, resolved_addr=None):
        self._resolver_address = resolver_address
        self._resolved_addr = resolved_addr

    def contract(self, address, abi):
        fn_names = {f.get("name") for f in abi}
        if "resolver" in fn_names:
            return _FakeContract(_FakeRegistryFunctions(self._resolver_address))
        return _FakeContract(_FakeResolverFunctions(self._resolved_addr))


class _FakeW3:
    def __init__(self, resolver_address=None, resolved_addr=None):
        self.eth = _FakeEth(resolver_address, resolved_addr)

    def to_checksum_address(self, addr):
        return addr


def test_namehash_matches_eip137_test_vector():
    """namehash("eth") is a well-known published value -- the strongest
    correctness check available for this algorithm."""
    assert bn.namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"


def test_namehash_empty_name_is_zero_node():
    assert bn.namehash("").hex() == "00" * 32


def test_resolve_basename_happy_path_uses_registrys_actual_resolver():
    """The core lesson from the real round-trip test (07/24): a name's
    resolver is NOT always the well-known default -- always query the
    Registry first."""
    w3 = _FakeW3(resolver_address=RESOLVER, resolved_addr=RESOLVED_ADDR)
    result = bn.resolve_basename("jesse.base.eth", w3=w3)
    assert result == RESOLVED_ADDR


def test_resolve_basename_no_registered_resolver_returns_none():
    w3 = _FakeW3(resolver_address=bn._ZERO_RESOLVER, resolved_addr=RESOLVED_ADDR)
    assert bn.resolve_basename("neverregistered.base.eth", w3=w3) is None


def test_resolve_basename_resolver_has_no_address_record_returns_none():
    w3 = _FakeW3(resolver_address=RESOLVER, resolved_addr=bn._ZERO_ADDRESS)
    assert bn.resolve_basename("noaddressrecord.base.eth", w3=w3) is None


def test_resolve_basename_empty_name_returns_none_without_rpc_call():
    class _ExplodingW3:
        def __getattr__(self, item):
            raise AssertionError("must never touch the RPC client for an empty name")

    assert bn.resolve_basename("", w3=_ExplodingW3()) is None
    assert bn.resolve_basename("   ", w3=_ExplodingW3()) is None


def test_resolve_basename_rpc_failure_degrades_to_none_never_raises():
    class _RaisingEth:
        def contract(self, address, abi):
            raise ConnectionError("RPC down")

    class _RaisingW3:
        eth = _RaisingEth()

        def to_checksum_address(self, addr):
            return addr

    assert bn.resolve_basename("jesse.base.eth", w3=_RaisingW3()) is None


def test_resolve_basename_normalizes_case_and_whitespace():
    w3 = _FakeW3(resolver_address=RESOLVER, resolved_addr=RESOLVED_ADDR)
    assert bn.resolve_basename("  Jesse.Base.Eth  ", w3=w3) == RESOLVED_ADDR
