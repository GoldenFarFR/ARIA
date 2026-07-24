"""Basenames forward resolution (name -> address) via direct Base RPC reads
(07/24, "build in-house" answer to paying an x402 ENS resolver -- several
found at $0.001-0.01/call, cf. the Bazaar scan, but this data is free and
public on-chain).

The REVERSE direction (address -> name) already exists elsewhere for free:
``services/blockscout.py``'s ``get_address_info()`` already fills
``AddressInfo.ens_domain_name``, already wired into ``smart_money.py``
(``card.display_name``) for /walletscore and /topwallets -- this module does
NOT duplicate that. Only the forward direction (a declared name -> its
address) was genuinely missing.

Two real, independently-verified facts this module is built on (07/24, never
guessed from memory or a single web fetch):

1. Contract addresses -- confirmed via Blockscout's own contract-verification
   API (not just a web fetch of Coinbase's docs, which could have
   transcribed a digit wrong): ``REGISTRY_ADDRESS`` is Blockscout-verified as
   "Registry"; the well-known default resolver proxy is Blockscout-verified
   as a ``TransparentUpgradeableProxy`` whose implementation is named
   "UpgradeableL2Resolver".

2. CRITICAL architecture fact discovered while validating end-to-end (never
   assume a single fixed resolver): a name's resolver is NOT always the
   default L2Resolver above -- standard ENS lets a name owner register any
   custom resolver via the Registry. A real round-trip test against
   "jesse.base.eth" (independently confirmed via Blockscout's
   ``ens_domain_name`` field on the target address) returned an all-zero
   address when calling ``addr()`` directly on the well-known default
   resolver, but resolved CORRECTLY once ``Registry.resolver(node)`` was
   queried FIRST to find this name's actual (non-default) resolver. This
   module therefore ALWAYS does the 2-step Registry -> Resolver lookup,
   never assumes the default resolver applies.
"""
from __future__ import annotations

import os

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# Basenames Registry (ENS-standard registry deployed on Base) -- confirmed
# Blockscout-verified, name "Registry" (07/24).
REGISTRY_ADDRESS = "0xb94704422c2a1e396835a571837aa5ae53285a95"

_REGISTRY_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "node", "type": "bytes32"}],
        "name": "resolver",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Standard ENS resolver `addr(bytes32)` -- the resolver address itself is
# NEVER assumed fixed (see module docstring); this ABI fragment is applied to
# whatever resolver `Registry.resolver(node)` returns for a given name.
_RESOLVER_ADDR_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "node", "type": "bytes32"}],
        "name": "addr",
        "outputs": [{"internalType": "address payable", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ZERO_RESOLVER = "0x0000000000000000000000000000000000000000"


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


def namehash(name: str) -> bytes:
    """Standard EIP-137 namehash. Validated against the well-known test
    vector (``namehash("eth")`` == the value published in the EIP)."""
    from web3 import Web3

    node = b"\x00" * 32
    if not name:
        return node
    for label in reversed(name.split(".")):
        node = Web3.keccak(node + Web3.keccak(text=label))
    return node


def resolve_basename(name: str, *, w3=None) -> str | None:
    """Resolves a Basename (e.g. "someone.base.eth") to its address.
    `None` if the name has no registered resolver, no address record, or the
    RPC read fails for any reason -- never blocking, never a fabricated
    address (same doctrine as base_onchain.py)."""
    if not name or not name.strip():
        return None
    try:
        client = _client(w3=w3)
        node = namehash(name.strip().lower())

        registry = client.eth.contract(
            address=client.to_checksum_address(REGISTRY_ADDRESS), abi=_REGISTRY_ABI
        )
        resolver_address = registry.functions.resolver(node).call()
        if not resolver_address or resolver_address == _ZERO_RESOLVER:
            return None

        resolver = client.eth.contract(
            address=client.to_checksum_address(resolver_address), abi=_RESOLVER_ADDR_ABI
        )
        address = resolver.functions.addr(node).call()
        if not address or address == _ZERO_ADDRESS:
            return None
        return client.to_checksum_address(address)
    except Exception:
        return None
