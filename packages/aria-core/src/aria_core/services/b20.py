"""Base's B20 native token standard -- detection + issuer-power reader (31/07).

Real gap this module closes (backlog #228, closed by a 2-agent diligence
workflow this same day): B20 tokens are RUST PRECOMPILES, not Solidity
contracts (activated on Base mainnet 2026-07-08) -- there is no bytecode to
analyze. Verified live in that workflow: GoPlus's Token Security API returns
HTTP 200 "OK" with real market data for a genuine B20 token, but SILENTLY
OMITS every honeypot/mint/ownership risk field from the JSON (not even
``"0"`` -- the keys are simply absent). A naive ``data.get("is_mintable") ==
"1"`` check downstream reads that as "no risk", not "unknown" -- a B20 with
an active, un-renounced mint/freeze/seize power would be bought with zero
warning. Blockscout has no B20-aware handling either (lists them as plain
unverified ERC-20s, `is_verified=False`, `creation_transaction_hash=None`).

Two capabilities, both real network calls, both best-effort (degrade to
``None``/``unresolved`` on any failure -- never a fabricated verdict):

1. ``is_b20`` -- calls the FACTORY precompile's own ``isB20(address)``
   (never a text-prefix guess on the address). Confirmed necessary the same
   workflow: 2 of 4 addresses that started with the hex string "b20" were
   NOT real B20 tokens (a plain ERC-20 and an EIP-1167 clone whose address
   happened to start that way) -- ``isB20`` is the only authority that can't
   be spoofed.

2. ``evaluate_b20_safety`` -- reconstructs who CURRENTLY holds the 3 roles
   that matter for a trading candidate (``MINT_ROLE``/``PAUSE_ROLE``/
   ``BURN_BLOCKED_ROLE`` -- freeze-and-seize) by replaying the token's own
   ``RoleGranted``/``RoleRevoked`` event log (B20 implements plain OZ
   ``AccessControl``, confirmed via ``base/base-std``'s real interface --
   there is NO enumeration function, ``getRoleMember`` doesn't exist, so
   replaying the log is the only way to answer "who holds this role now"
   without already knowing which address to ask). B20 tokens CAN go fully
   admin-less (a dedicated ``renounceLastAdmin()`` exists, confirmed in the
   same interface) -- this module distinguishes that genuinely-renounced
   case (verdict "safe") from a still-active role holder (verdict "risky"),
   never blanket-rejecting every B20 by construction.

Known, accepted limitation: a public RPC node's ``eth_getLogs`` enforces a
narrow block-range window (confirmed empirically well under the "10,000"
figure the node itself advertises in its own error message). Scanning is
therefore paginated in small windows, capped at ``MAX_LOG_SCAN_WINDOWS`` --
if the token's creation block lies beyond that horizon, the scan returns
``complete=False`` and the caller must treat the token as opaque (same
fail-closed doctrine already applied to an unverified contract in the VC
crible), never assume "no grants found" means "none exist". In practice a
momentum-pipeline candidate is freshly discovered (hours/days old), so this
horizon is rarely the real constraint -- but it is a real, documented one for
an older B20."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_RPC_URL = "https://mainnet.base.org"

# The B20Factory precompile -- the ONE authority that can't be spoofed by a
# vanity/lookalike address (confirmed live, github.com/base/base-std,
# src/interfaces/IB20Factory.sol).
B20_FACTORY_ADDRESS = "0xB20f000000000000000000000000000000000000"

_FACTORY_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "isB20",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "token", "type": "address"}],
        "name": "isB20Initialized",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Minimal AccessControl surface every B20 token exposes (base/base-std,
# src/interfaces/IB20.sol) -- role constants are getter functions (each
# returns its own bytes32 role id, never assumed/hardcoded here since a role
# id is a keccak256 hash the caller must read from the contract itself).
_ROLE_CONSTANT_ABI_TEMPLATE = {
    "inputs": [],
    "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
    "stateMutability": "view",
    "type": "function",
}

# The 3 roles that matter for a trading candidate's safety -- MINT_ROLE
# (uncapped supply inflation), PAUSE_ROLE (freeze all transfers), BURN_
# BLOCKED_ROLE (the real "seize" power: burn a specific blocked holder's
# balance). Deliberately NOT the full 7-role taxonomy (e.g. METADATA_ROLE
# has no trading-safety implication) -- scope matches the real risk this
# module exists to cover.
SENSITIVE_ROLES = ("MINT_ROLE", "PAUSE_ROLE", "BURN_BLOCKED_ROLE")

_ROLE_GRANTED_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "internalType": "bytes32", "name": "role", "type": "bytes32"},
        {"indexed": True, "internalType": "address", "name": "account", "type": "address"},
        {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
    ],
    "name": "RoleGranted",
    "type": "event",
}
_ROLE_REVOKED_EVENT_ABI = dict(_ROLE_GRANTED_EVENT_ABI, name="RoleRevoked")

_TOKEN_ABI = (
    [dict(_ROLE_CONSTANT_ABI_TEMPLATE, name=role) for role in SENSITIVE_ROLES]
    + [_ROLE_GRANTED_EVENT_ABI, _ROLE_REVOKED_EVENT_ABI]
)

# Empirically calibrated (31/07, live test against the public Base RPC): the
# node's own error message advertises a 10,000-block range limit, but a
# 9,999-block window still failed -- 900 blocks succeeded. Kept well under
# that observed ceiling, not the advertised one (same "measure, don't trust
# the doc" doctrine as docs/api-rate-limit-calibration.md).
LOG_SCAN_WINDOW_BLOCKS = 800
# Caps total RPC calls per role scan (2 events x this many windows). At
# ~2s/block on Base, 300 windows x 800 blocks = 240,000 blocks =~ 5.5 days --
# comfortably covers a freshly-discovered momentum candidate (hours/days
# old); an older B20 whose creation lies beyond this horizon degrades to
# "incomplete", never a false "no holder found".
MAX_LOG_SCAN_WINDOWS = 300


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


async def is_b20(token_address: str, *, w3=None) -> bool | None:
    """``True``/``False`` from the factory's own authority, ``None`` on any
    RPC failure (never guessed from the address text -- see module
    docstring for why a prefix match alone produces false positives)."""
    if not token_address:
        return None
    try:
        client = _client(w3=w3)
        factory = client.eth.contract(
            address=client.to_checksum_address(B20_FACTORY_ADDRESS), abi=_FACTORY_ABI
        )
        return bool(
            factory.functions.isB20(client.to_checksum_address(token_address)).call()
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocking
        logger.info("b20: is_b20 check failed for %s (%s)", token_address, exc)
        return None


@dataclass
class RoleHolderScan:
    """``complete=False`` means the scan hit ``MAX_LOG_SCAN_WINDOWS`` before
    reaching the token's own creation -- ``holders`` may be missing older
    grants in that case, the caller MUST NOT treat an empty ``holders`` as
    "renounced" when ``complete`` is False (see ``evaluate_b20_safety``)."""

    complete: bool
    holders: set[str] = field(default_factory=set)


# A grant right at token creation is the common case (an admin role is
# almost always set up in the deployment tx or shortly after) -- 15 empty
# windows (~12,000 blocks, ~7h on Base) beyond the last real grant is a
# generous margin before concluding "scanned back far enough", without
# forcing every scan to walk the full MAX_LOG_SCAN_WINDOWS horizon.
_EARLY_STOP_EMPTY_WINDOWS = 15


def _window_logs(contract, event_name: str, *, from_block: int, to_block: int, role_id) -> list[dict]:
    """Synchronous, blocking web3 call -- ALWAYS run via ``asyncio.to_thread``
    by the caller, never awaited directly (web3.py's HTTPProvider has no
    native async mode here)."""
    event = getattr(contract.events, event_name)
    return list(event.get_logs(from_block=from_block, to_block=to_block, argument_filters={"role": role_id}))


# 31/07 -- real performance problem found live: the naive sequential version
# of this scan (one window, then the next, awaited one at a time) took
# 118.7s to conclude "incomplete" on a real 23-day-old B20 token against the
# public Base RPC -- far too slow for a pipeline whose whole doctrine is
# "ARIA must be first". Windows are independent reads (no dependency between
# them beyond the chronological ORDER they're interpreted in for the early-
# stop heuristic) -- fetched in parallel batches via asyncio.gather, only the
# early-stop decision itself stays sequential (applied once a full batch is
# back, walking it newest-to-oldest exactly as the old serial loop did).
_PARALLEL_BATCH_SIZE = 20


async def scan_role_holders(
    token_address: str, role_name: str, *, w3=None
) -> RoleHolderScan | None:
    """Reconstructs the CURRENT holder set of one role by replaying its
    ``RoleGranted``/``RoleRevoked`` history, paginated in
    ``LOG_SCAN_WINDOW_BLOCKS``-sized windows walking backward from the chain
    tip, up to ``MAX_LOG_SCAN_WINDOWS`` -- fetched ``_PARALLEL_BATCH_SIZE``
    windows at a time (see the module-level comment above for why this isn't
    sequential). Stops early once a window returns zero events on BOTH event
    types for ``_EARLY_STOP_EMPTY_WINDOWS`` consecutive windows AND at least
    one grant has already been seen -- a heuristic (not a proof the scan
    reached genesis), but the earliest real grant almost always sits right
    around token creation, so a long empty stretch beyond that is a strong
    signal the wallet's history is fully covered. Returns ``None`` on a hard
    failure (RPC unreachable for the role-constant read itself)."""
    if not token_address or role_name not in SENSITIVE_ROLES:
        return None
    try:
        client = _client(w3=w3)
        checksum = client.to_checksum_address(token_address)
        contract = client.eth.contract(address=checksum, abi=_TOKEN_ABI)
        role_id = getattr(contract.functions, role_name)().call()
    except Exception as exc:  # noqa: BLE001
        logger.info("b20: role constant read failed for %s/%s (%s)", token_address, role_name, exc)
        return None

    try:
        latest = client.eth.block_number
    except Exception as exc:  # noqa: BLE001
        logger.info("b20: block_number read failed for %s (%s)", token_address, exc)
        return None

    # Pre-compute every window's (from_block, to_block) bounds up front,
    # newest-to-oldest, capped at MAX_LOG_SCAN_WINDOWS -- batching just
    # slices this fixed list, the early-stop logic below still walks it in
    # this exact chronological order.
    windows: list[tuple[int, int]] = []
    to_block = latest
    for _ in range(MAX_LOG_SCAN_WINDOWS):
        from_block = max(0, to_block - LOG_SCAN_WINDOW_BLOCKS + 1)
        windows.append((from_block, to_block))
        if from_block == 0:
            break
        to_block = from_block - 1

    holders: set[str] = set()
    complete = False
    consecutive_empty = 0
    for batch_start in range(0, len(windows), _PARALLEL_BATCH_SIZE):
        batch = windows[batch_start : batch_start + _PARALLEL_BATCH_SIZE]
        try:
            fetched = await asyncio.gather(
                *[
                    asyncio.gather(
                        asyncio.to_thread(
                            _window_logs, contract, "RoleGranted",
                            from_block=fb, to_block=tb, role_id=role_id,
                        ),
                        asyncio.to_thread(
                            _window_logs, contract, "RoleRevoked",
                            from_block=fb, to_block=tb, role_id=role_id,
                        ),
                    )
                    for fb, tb in batch
                ]
            )
        except Exception as exc:  # noqa: BLE001 -- treat as incomplete, never crash the scan
            logger.info(
                "b20: get_logs batch failed for %s/%s starting at window %d (%s)",
                token_address, role_name, batch_start, exc,
            )
            return RoleHolderScan(complete=False, holders=holders)

        for (from_block, _to_block), (granted, revoked) in zip(batch, fetched):
            for entry in sorted(list(granted) + list(revoked), key=lambda e: (e["blockNumber"], e["logIndex"])):
                account = entry["args"]["account"]
                if entry["event"] == "RoleGranted":
                    holders.add(account)
                else:
                    holders.discard(account)

            if not granted and not revoked:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            if from_block == 0:
                complete = True
                break
            if holders and consecutive_empty >= _EARLY_STOP_EMPTY_WINDOWS:
                complete = True
                break
        else:
            continue
        break

    return RoleHolderScan(complete=complete, holders=holders)


@dataclass
class B20SafetyVerdict:
    """``verdict``: "not_b20" (the address isn't a real B20 -- caller should
    fall back to the normal pipeline), "safe" (every sensitive role
    genuinely renounced, confirmed complete), "risky" (at least one
    sensitive role still held by a real address), or "opaque" (B20 confirmed,
    but at least one role's history couldn't be fully scanned -- fail-closed,
    same doctrine as an unverified contract in the VC crible). ``role_holders``
    maps role name -> set of holder addresses (empty set only meaningful
    when the corresponding scan was ``complete``)."""

    verdict: str
    role_holders: dict[str, set[str]] = field(default_factory=dict)
    reason: str = ""


async def evaluate_b20_safety(token_address: str, *, w3=None) -> B20SafetyVerdict:
    """The single entry point a caller (momentum/VC gate) needs. Never
    raises -- every failure degrades to a verdict the caller can act on
    directly (fail-closed: "opaque" on any unresolved scan, never a silent
    "safe" out of missing data)."""
    b20 = await is_b20(token_address, w3=w3)
    if b20 is None:
        return B20SafetyVerdict(verdict="opaque", reason="isB20() lookup failed -- factory unreachable")
    if not b20:
        return B20SafetyVerdict(verdict="not_b20")

    role_holders: dict[str, set[str]] = {}
    for role_name in SENSITIVE_ROLES:
        scan = await scan_role_holders(token_address, role_name, w3=w3)
        if scan is None:
            return B20SafetyVerdict(
                verdict="opaque", reason=f"{role_name} constant unreachable -- role holders unknown",
            )
        if not scan.complete:
            return B20SafetyVerdict(
                verdict="opaque",
                reason=f"{role_name} history scan incomplete (creation block beyond scan horizon)",
                role_holders={role_name: scan.holders},
            )
        role_holders[role_name] = scan.holders

    active_roles = {name: holders for name, holders in role_holders.items() if holders}
    if not active_roles:
        return B20SafetyVerdict(
            verdict="safe", role_holders=role_holders,
            reason="every sensitive role confirmed renounced (no current holder)",
        )
    return B20SafetyVerdict(
        verdict="risky", role_holders=role_holders,
        reason="active holder(s) on: " + ", ".join(sorted(active_roles)),
    )
