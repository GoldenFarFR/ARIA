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
therefore paginated in small windows, walking backward from the chain tip
down to the token's own CREATION BLOCK -- found via binary search on
``eth_getCode`` (``_find_creation_block``), since neither Blockscout nor any
other indexer can give a creation block for a precompile-instantiated token
(confirmed live: ``creation_transaction_hash`` is ``null`` even for a real
B20 that Blockscout otherwise recognizes). A first version of this module
bounded the scan at a FIXED window count instead (~5.5 days of history) --
confirmed live to silently return "opaque" for a real 23-day-old B20 (block
time on Base is 2.0s, not what that first calibration assumed), which would
have made this check useless on anything but a same-week-old token. The
creation-block search removes that ceiling entirely -- the scan always
reaches exactly as far back as it needs to, regardless of the token's real
age. ``MAX_LOG_SCAN_WINDOWS`` remains only as a generous safety backstop
(a pathological creation-block resolution failure), never the primary
limiter now.

Known, UNRESOLVED limitation (31/07, verified live, not just theorized): the
public Base RPC's real ceiling on CONCURRENT ``eth_getLogs`` calls sits
between 26 (succeeds) and 28 (real HTTP 429) -- ``_PARALLEL_BATCH_SIZE``
calibrated down to 8 (16 concurrent calls/batch) with a short retry-with-
backoff on 429 in ``_window_logs``. This closes most of the gap but NOT all
of it: on a genuinely old B20 (~23 days, ~1,233 windows), two back-to-back
live runs against the real public RPC took 488s (succeeded) and 572s
(exhausted its retries, degraded to "opaque") -- the node's real capacity
fluctuates with its own shared load, not something a client-side constant
alone can fully compensate for. For a FRESHLY-discovered candidate (hours to
a few days old, the momentum pipeline's actual real-world case -- confirmed
live: a ~1,800-block/~6h-old scan resolved in 0.6s, first try, every time)
this is a non-issue. It only bites an OLDER token: the manual VC crible
(``/vc <any contract>``) and any future x402 product that could be asked
about an arbitrary token are the paths actually exposed to it. The real fix
is a dedicated/paid RPC provider with a verified throughput (not the free
public node) -- tracked as a prerequisite before selling this via x402,
never silently worked around by guessing another client-side constant."""
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
# Safety BACKSTOP only (31/07, real bug found live: this used to be the
# PRIMARY limiter at 300 -- 240,000 blocks =~ 5.5 days on Base's real 2.0s
# block time -- and silently forced "opaque" on a real 23-day-old B20).
# Since the scan now walks exactly down to the token's real creation block
# (found via `_find_creation_block`, never guessed), this cap should never
# realistically be hit for a genuine B20 -- it only guards against a
# pathological creation-block resolution bug. ~10,000 windows =~ 8,000,000
# blocks =~ 185 days on Base, comfortably beyond B20's entire lifetime as a
# standard so far (activated 2026-07-08).
MAX_LOG_SCAN_WINDOWS = 10_000


def _rpc_url() -> str:
    return (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _client(*, w3=None):
    if w3 is not None:
        return w3
    from web3 import Web3

    return Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 10}))


async def _find_creation_block(client, checksum_address: str, latest: int) -> int | None:
    """Binary search via ``eth_getCode`` for the earliest block at which the
    B20 precompile already has code. Blockscout has no creation-block index
    for a precompile-instantiated token (confirmed live: creation_transaction_hash
    is null even for a real B20 it otherwise recognizes) -- this is the only
    reliable way to bound the role-history scan to the token's REAL age.
    ``~log2(latest)`` sequential calls (one token, ~26 calls on Base's real
    chain height) -- run ONCE per token by the caller (``evaluate_b20_safety``),
    never per-role. Fails closed (``None``) on any RPC error -- the caller
    then treats the scan as opaque, never guesses a block."""
    try:
        code_at_latest = await asyncio.to_thread(client.eth.get_code, checksum_address, latest)
    except Exception as exc:  # noqa: BLE001
        logger.info("b20: get_code(latest) failed for %s (%s)", checksum_address, exc)
        return None
    if not code_at_latest:
        return None  # no code even at the chain tip -- not really deployed here
    try:
        code_at_zero = await asyncio.to_thread(client.eth.get_code, checksum_address, 0)
    except Exception as exc:  # noqa: BLE001
        logger.info("b20: get_code(0) failed for %s (%s)", checksum_address, exc)
        return None
    if code_at_zero:
        return 0  # existed since genesis (not expected for B20, but correct)

    lo, hi = 0, latest  # invariant: code absent at lo, present at hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        try:
            code = await asyncio.to_thread(client.eth.get_code, checksum_address, mid)
        except Exception as exc:  # noqa: BLE001
            logger.info("b20: get_code(%d) failed for %s (%s)", mid, checksum_address, exc)
            return None
        if code:
            hi = mid
        else:
            lo = mid
    return hi


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


# 31/07 -- real bug found live testing the creation-block fix above: a batch
# of _PARALLEL_BATCH_SIZE=20 windows x 2 event types = 40 concurrent
# eth_getLogs calls hit a real HTTP 429 on the public Base RPC on the very
# first batch of a genuinely-aged token's scan. Empirically measured (not
# guessed, same doctrine as docs/api-rate-limit-calibration.md): 26
# concurrent calls succeeded, 28 failed -- the real ceiling sits right there.
# A short retry-with-backoff on 429 alone (never on a genuine/permanent
# failure) absorbs a transient burst without derailing the whole scan.
_LOG_FETCH_MAX_RETRIES = 3
_LOG_FETCH_RETRY_BASE_DELAY_S = 0.5


def _window_logs(contract, event_name: str, *, from_block: int, to_block: int, role_id) -> list[dict]:
    """Synchronous, blocking web3 call -- ALWAYS run via ``asyncio.to_thread``
    by the caller, never awaited directly (web3.py's HTTPProvider has no
    native async mode here). Retries a rate-limit response (429) with a short
    backoff -- ``time.sleep`` is safe here since this already runs in its own
    thread, never the main event loop."""
    import time

    event = getattr(contract.events, event_name)
    for attempt in range(_LOG_FETCH_MAX_RETRIES):
        try:
            return list(
                event.get_logs(from_block=from_block, to_block=to_block, argument_filters={"role": role_id})
            )
        except Exception as exc:  # noqa: BLE001
            is_rate_limited = "429" in str(exc) or "Too Many Requests" in str(exc)
            if not is_rate_limited or attempt == _LOG_FETCH_MAX_RETRIES - 1:
                raise
            time.sleep(_LOG_FETCH_RETRY_BASE_DELAY_S * (2**attempt))
    return []  # unreachable (loop always returns or raises), keeps type-checkers happy


# 31/07 -- real performance problem found live: the naive sequential version
# of this scan (one window, then the next, awaited one at a time) took
# 118.7s to conclude "incomplete" on a real 23-day-old B20 token against the
# public Base RPC -- far too slow for a pipeline whose whole doctrine is
# "ARIA must be first". Windows are independent reads (no dependency between
# them beyond the chronological ORDER they're interpreted in for the early-
# stop heuristic) -- fetched in parallel batches via asyncio.gather, only the
# early-stop decision itself stays sequential (applied once a full batch is
# back, walking it newest-to-oldest exactly as the old serial loop did).
#
# Value calibrated DOWN the same day (real 429 hit live testing the
# creation-block fix): each window fires 2 concurrent requests (RoleGranted +
# RoleRevoked), so the original 20 meant 40 concurrent eth_getLogs calls --
# empirically measured against the real public Base RPC (mainnet.base.org):
# 26 concurrent calls succeeded, 28 failed with a real HTTP 429. 8 windows =
# 16 concurrent calls, comfortably under that measured ceiling with margin
# for the RPC being shared with the rest of ARIA's own concurrent traffic at
# the same time (same "90% of measured capacity, never guessed" doctrine as
# docs/api-rate-limit-calibration.md).
_PARALLEL_BATCH_SIZE = 8


async def scan_role_holders(
    token_address: str, role_name: str, *, w3=None, creation_block: int | None = None,
) -> RoleHolderScan | None:
    """Reconstructs the CURRENT holder set of one role by replaying its
    ``RoleGranted``/``RoleRevoked`` history, paginated in
    ``LOG_SCAN_WINDOW_BLOCKS``-sized windows walking backward from the chain
    tip down to the token's own creation block -- fetched ``_PARALLEL_BATCH_
    SIZE`` windows at a time (see the module-level comment above for why this
    isn't sequential). Stops early once a window returns zero events on BOTH
    event types for ``_EARLY_STOP_EMPTY_WINDOWS`` consecutive windows AND at
    least one grant has already been seen -- a heuristic (not a proof the
    scan reached creation), but the earliest real grant almost always sits
    right around token creation, so a long empty stretch beyond that is a
    strong signal the wallet's history is fully covered. Returns ``None`` on
    a hard failure (RPC unreachable for the role-constant read itself).

    ``creation_block`` (31/07): pass the token's real creation block (found
    via ``_find_creation_block``) to bound the scan precisely instead of
    the fixed ``MAX_LOG_SCAN_WINDOWS`` window count that used to be the
    primary limiter (real bug: too short for anything but a same-week-old
    B20, see module docstring). ``evaluate_b20_safety`` resolves this ONCE
    per token and passes it to all 3 role scans -- if omitted (e.g. direct
    callers/tests), it's resolved here instead."""
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

    if creation_block is None:
        creation_block = await _find_creation_block(client, checksum, latest)
        if creation_block is None:
            logger.info(
                "b20: creation block unresolved for %s -- role scan treated as incomplete", token_address,
            )
            return RoleHolderScan(complete=False, holders=set())

    # Pre-compute every window's (from_block, to_block) bounds up front,
    # newest-to-oldest, down to creation_block, capped at MAX_LOG_SCAN_WINDOWS
    # (safety backstop, see its own comment) -- batching just slices this
    # fixed list, the early-stop logic below still walks it in this exact
    # chronological order.
    windows: list[tuple[int, int]] = []
    to_block = latest
    for _ in range(MAX_LOG_SCAN_WINDOWS):
        from_block = max(creation_block, to_block - LOG_SCAN_WINDOW_BLOCKS + 1)
        windows.append((from_block, to_block))
        if from_block <= creation_block:
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

            if from_block <= creation_block:
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
    "safe" out of missing data).

    Resolves the token's creation block ONCE here (``_find_creation_block``)
    and reuses it for all 3 role scans -- avoids 3 redundant binary searches
    (creation block is a property of the token, not the role). The 3 scans
    are then independent given that shared bound, so they run concurrently
    (``asyncio.gather``) rather than one after another."""
    b20 = await is_b20(token_address, w3=w3)
    if b20 is None:
        return B20SafetyVerdict(verdict="opaque", reason="isB20() lookup failed -- factory unreachable")
    if not b20:
        return B20SafetyVerdict(verdict="not_b20")

    client = _client(w3=w3)
    try:
        checksum = client.to_checksum_address(token_address)
        latest = client.eth.block_number
    except Exception as exc:  # noqa: BLE001
        logger.info("b20: block height unreachable for %s (%s)", token_address, exc)
        return B20SafetyVerdict(verdict="opaque", reason="block height unreachable")
    creation_block = await _find_creation_block(client, checksum, latest)
    if creation_block is None:
        return B20SafetyVerdict(
            verdict="opaque", reason="creation block unresolved -- can't bound the role history scan",
        )

    scans = await asyncio.gather(
        *[
            scan_role_holders(token_address, role_name, w3=w3, creation_block=creation_block)
            for role_name in SENSITIVE_ROLES
        ]
    )

    role_holders: dict[str, set[str]] = {}
    for role_name, scan in zip(SENSITIVE_ROLES, scans):
        if scan is None:
            return B20SafetyVerdict(
                verdict="opaque", reason=f"{role_name} constant unreachable -- role holders unknown",
            )
        if not scan.complete:
            return B20SafetyVerdict(
                verdict="opaque",
                reason=f"{role_name} history scan incomplete",
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
