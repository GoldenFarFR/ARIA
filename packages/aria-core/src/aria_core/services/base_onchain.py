"""Base mainnet on-chain reads, read-only -- no key, no signing (audit 11/07).

Complements ``services/virtuals.py::graduation_progress`` (which stays `None`
for lack of API data) with a real on-chain read, gated OFF by default. Unlike
``onchain/sepolia_wallet.py`` (holds a key, signs testnet transactions), this
module ONLY does public ``eth_call``s (``getReserves``, `tokenGradThreshold``)
-- same guarantee as ``services/blockscout.py``, no financial risk possible by
construction.

## What is confirmed (11/07 investigation, cf. comment above
## ``_VIRTUAL_RAISED_KEYS`` in ``virtuals.py`` for the full detail)

- The agent token's initial reserve in the bonding pair is ALWAYS
  ``INITIAL_TOKEN_RESERVE`` (1 billion, also confirmed by the API's
  `totalSupply` field) -- verified identical across several freshly launched
  tokens.
- The graduation threshold is PER TOKEN (``tokenGradThreshold(address)`` on
  the Bonding V5 contract, not a global constant) -- formula empirically
  validated on a real graduated token: `reserve0 <= tokenGradThreshold` at the
  moment of graduation, exactly the source contract's condition
  (`BondingV5.sol::_buy`).

## Honest limitation -- NOT resolved

Several instances of the Bonding V5 contract coexist; ``BONDING_V5_CONTRACT``
below only covers ONE of them (the one where a real `Graduated` event was
found by scanning logs). A token managed by another instance returns
`tokenGradThreshold == 0` (never registered) -- treated as "no data",
degrading to `None`, never a fake value. An honest partial coverage, not a
bug.
"""
from __future__ import annotations

import os

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# Only one known instance of the Bonding V5 contract (Base mainnet), confirmed
# by directly scanning on-chain logs (`Graduated(address,address)`) and
# verified twice: a real non-empty `tokenInfo()` for a genuinely graduated
# token, and a real non-zero `tokenGradThreshold()` for that same token AND two
# different freshly launched tokens (WOODY, CRASHCAT).
BONDING_V5_CONTRACT = "0x1A540088125d00dD3990f9dA45CA0859af4d3B01"

# Initial reserve of the agent token in the pair -- confirmed constant (not a
# guess) across 4 different freshly launched tokens, and consistent with the
# API's `totalSupply` field.
INITIAL_TOKEN_RESERVE = 1_000_000_000.0

# Minimal ABI fragments -- only the functions this module reads.
_GET_RESERVES_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint256", "name": "reserve0", "type": "uint256"},
            {"internalType": "uint256", "name": "reserve1", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
_TOKEN_GRAD_THRESHOLD_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "", "type": "address"}],
        "name": "tokenGradThreshold",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]
# Standard ERC20 reads -- same two functions on every token, never
# token-specific. 10/08: added as a rescue path for
# momentum_entry._check_holder_concentration during a real total Blockscout
# outage (base.blockscout.com returning HTTP 500 on every endpoint,
# including get_token_metadata) -- see _fetch_erc20_metadata's own docstring.
_ERC20_METADATA_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def onchain_graduation_enabled() -> bool:
    """Additive gate -- no eth_call is even attempted without this flag (fail-closed)."""
    return os.environ.get("ARIA_ONCHAIN_GRADUATION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


def fetch_pair_reserve0(pair_address: str, *, w3=None) -> float | None:
    """Current reserve of the agent token (``reserve0``) in the bonding pair,
    in whole units (already divided by 1e18). `None` if the address is
    invalid, the RPC fails, or the read fails for any other reason -- never
    blocking, never an exception propagating to the caller."""
    if not pair_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(pair_address), abi=_GET_RESERVES_ABI
        )
        reserve0, _reserve1 = contract.functions.getReserves().call()
        return float(reserve0) / 1e18
    except Exception:
        return None


def fetch_token_grad_threshold(token_address: str, *, w3=None) -> float | None:
    """This token's own graduation threshold, read on `BONDING_V5_CONTRACT`.
    `None` if the token isn't registered on THIS instance of the contract
    (known partial coverage, not an error) or if the read fails."""
    if not token_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(BONDING_V5_CONTRACT),
            abi=_TOKEN_GRAD_THRESHOLD_ABI,
        )
        threshold_wei = contract.functions.tokenGradThreshold(
            client.to_checksum_address(token_address)
        ).call()
        threshold = float(threshold_wei) / 1e18
        # threshold == 0 -> never registered on this instance (not a genuine zero threshold).
        if threshold <= 0:
            return None
        return threshold
    except Exception:
        return None


def fetch_erc20_metadata(token_address: str, *, w3=None) -> tuple[int, int] | None:
    """``(decimals, total_supply_raw)`` read DIRECTLY on-chain via a public
    RPC -- ``total_supply_raw`` in the token's smallest unit, NOT divided by
    ``10**decimals`` (caller's job, same convention as the rest of this
    pipeline). `None` on any failure (bad address, RPC down, non-ERC20
    contract) -- never a fabricated value, never an exception propagating.

    10/08 -- built as a rescue path for
    ``momentum_entry._check_holder_concentration``'s paid x402 fallback: that
    fallback ONLY returns raw per-holder balances (no decimals/total_supply
    of its own), so the caller has always needed a SEPARATE
    ``client.get_token_metadata()`` call to interpret them -- and that call
    went through the same ``base.blockscout.com`` host as the free holders
    endpoint. During the 10/08 outage (that host returning HTTP 500 on
    EVERY endpoint, not just holders), the paid x402 fallback was silently
    never even attempted -- the metadata call failed first and the
    guardrail degraded straight to "unavailable" without ever reaching
    x402. `decimals()`/`totalSupply()` are both part of the ERC20 standard
    itself -- reading them directly on-chain via RPC has zero dependency on
    Blockscout, free or paid, structurally immune to this exact class of
    outage."""
    if not token_address:
        return None
    try:
        client = _client(w3=w3)
        contract = client.eth.contract(
            address=client.to_checksum_address(token_address), abi=_ERC20_METADATA_ABI
        )
        decimals = int(contract.functions.decimals().call())
        total_supply = int(contract.functions.totalSupply().call())
        if total_supply <= 0:
            return None
        return decimals, total_supply
    except Exception:
        return None


def onchain_graduation_progress(
    *, pair_address: str | None, token_address: str | None, w3=None
) -> float | None:
    """Real progress (0.0-1.0) read on-chain, or `None` if unavailable for THIS
    token (honest partial coverage -- cf. module docstring). Gated by
    `onchain_graduation_enabled()`, never called otherwise. Formula empirically
    validated on 11/07 on a real graduated token (see `virtuals.py`)."""
    if not onchain_graduation_enabled():
        return None
    if not pair_address or not token_address:
        return None
    reserve0 = fetch_pair_reserve0(pair_address, w3=w3)
    threshold = fetch_token_grad_threshold(token_address, w3=w3)
    if reserve0 is None or threshold is None:
        return None
    if threshold >= INITIAL_TOKEN_RESERVE:
        # Inconsistent configuration (never observed in practice) -- don't
        # divide by zero or produce an absurd ratio, degrade cleanly.
        return None
    progress = (INITIAL_TOKEN_RESERVE - reserve0) / (INITIAL_TOKEN_RESERVE - threshold)
    return max(0.0, min(progress, 1.0))
