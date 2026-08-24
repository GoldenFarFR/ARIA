"""ARIA's homemade agent wallet, Robinhood Chain leg (17/08) — FIRST MILESTONE
ONLY: read-only verification against the real testnet, NO private key, NO
signing logic yet.

Context: the operator validated (17/08) a self-built wallet architecture over
every commercial provider evaluated (Coinbase CDP/MetaMask Agent
Wallet/Turnkey/MoonPay PayBox — see
``docs/HANDOFF_COINBASE_CDP.md``/memory ``project_wallet_provider_diligence_
solana_robinhood``) for a future modest real-capital pilot (~100$) on
Robinhood Chain + Solana. The chosen pattern (industry-standard dual-key
smart-contract-wallet): Safe{Wallet} + the official ``AllowanceModule``
(github.com/safe-global/safe-modules) — an "agent key" gets a spending
allowance structurally bounded ON-CHAIN (never just application-level), the
"owner key" (operator-held) keeps ultimate override control.

This module is deliberately scoped to the SAME "pre-mainnet rehearsal"
doctrine already established by ``sepolia_wallet.py``: prove the
infrastructure is real and reachable on a network where funds are worthless,
BEFORE any signing code is written. Both contract addresses below were
reported by a research agent (17/08 diligence) and then INDEPENDENTLY
RE-VERIFIED live against the real testnet RPC in the same session (real
``eth_getCode`` calls, non-empty bytecode confirmed for both, real
``eth_chainId`` confirmed == 46630) — never taken on the agent's word alone.
Re-run ``verify_contracts_deployed()`` before trusting these addresses again
in a future session; a redeploy or address change would break this silently
otherwise.

STEPS 1-4 DONE (17/08 -> 23/08): (1) read-only ABI wiring, (2) Safe creation +
module activation on testnet (``safe_robinhood_deploy.py``), (3) EIP-712
allowance-transfer signing/execution by the agent key
(``safe_robinhood_signer.py``), (4) real end-to-end testnet cycle proven live
23/08 (owner sets an allowance, agent key spends within it, an over-limit
spend rejected on-chain with a real revert) -- see
``docs/HANDOFF_AGENT_WALLET.md``.

Step (5), "mainnet proposal": the CONTRACT itself already lives on mainnet
(4663) -- re-verified live 24/08 via ``eth_getCode``, same CREATE2 address,
14908 bytes matching testnet exactly, ``eth_chainId`` confirmed 0x1237.
``ROBINHOOD_MAINNET_CHAIN_ID`` below and ``require_expected_chain()`` are the
SEAM that makes pointing this dome's write path at mainnet a one-parameter
change instead of a rewrite the day it's authorized -- they change NOTHING by
default (every caller in this dome still passes only
``ROBINHOOD_TESTNET_CHAIN_ID``, so a chain-id preflight still raises on
anything else, mainnet included). Actually spending real capital here still
needs its own separate, explicit operator decision on top of this: per
CLAUDE.md's own three named prerequisites (mainnet contract deployment --
DONE; the AllowanceModule v0.1.1-vs-v1.0.0 version decision -- RESOLVED 24/08,
there is no separate v1.0.0 AllowanceModule CONTRACT at all -- verified live
against both the official ``safe-fndn/safe-modules`` registry (only v0.1.0/
v0.1.1 ever shipped) and Candide's own deployments page
(docs.candide.dev/wallet/technical-reference/deployments/, which lists no
Allowance Module of any kind, and no Robinhood Chain coverage for ANY of its
modules). Candide's own "v1.0.0" documentation is their SDK/wrapper version,
not a new contract -- this dome calls the Safe module directly (no Candide
dependency), so it never applied here regardless. v0.1.1 (already in place,
``ALLOWANCE_MODULE_ADDRESS`` below) stands confirmed as the only real option,
Ackee's incremental audit remains the best available; wallet_guard/kill-switch
wiring -- the testnet rehearsal cycle below IS that wiring, proven on
worthless funds first), each one a distinct action, never grouped under a
single "ok".
"""
from __future__ import annotations

import os

ROBINHOOD_TESTNET_CHAIN_ID = 46630
_DEFAULT_TESTNET_RPC_URL = "https://rpc.testnet.chain.robinhood.com"

# Mainnet chain id (4663) -- NOT a default anywhere in this dome, listed here
# only so ``require_expected_chain`` has a real, named alternative to accept
# the day an explicit operator decision opts a caller into it (see module
# docstring, step 5). Re-verified live 24/08 (``eth_chainId`` -> 0x1237).
ROBINHOOD_MAINNET_CHAIN_ID = 4663
_DEFAULT_MAINNET_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"

# Safe v1.4.1 canonical singleton — same CREATE2 address as most EVM chains
# (safe-global/safe-deployments registry). Re-verified live 17/08: real
# bytecode present at this address on the testnet RPC above (23579 bytes).
SAFE_SINGLETON_V141_ADDRESS = "0x41675C099F32341bf84BFc5382aF534df5C7461a"

# AllowanceModule v0.1.1 — same CREATE2 address across 52 networks including
# Robinhood Chain mainnet (4663) and testnet (46630). Re-verified live 17/08:
# real bytecode present at this address on the testnet RPC above (14908
# bytes). The version is no longer an assumption: a real ``eth_call`` to
# ``VERSION()`` on the testnet returned "0.1.1" and ``NAME()`` returned
# "Allowance Module" (17/08, step-1 wiring session).
# NOTE (23/08, corrects a real drift): the 17/08 diligence claimed "no audit
# report found for 0.1.1" -- WRONG, found and read in full 23/08. Ackee
# Blockchain Security audited the exact two changes that make up 0.1.1 (PR
# safe-fndn/safe-modules#493, report "Safe -- Allowance module", rev 1.1,
# 09.09.2024): the EIP-712 transfer-typehash fix (rev 1.0, 1 engineer-day,
# scope limited to the changed typehash) and the resetTimeMin
# divide-by-zero guard, which Ackee's own review FOUND (not merely
# "suggested in passing") and classified Low; rev 1.1 confirms both fixed.
# Real nuance to keep: this is an INCREMENTAL, narrowly-scoped audit of the
# 0.1.0->0.1.1 diff (leaning on the original 0.1.0 audit for the rest of the
# contract), not a fresh full-contract audit -- meaningfully better than
# "unaudited" but short of a complete re-review. Same module, same two
# addresses, independently confirmed used by Candide's own Allowance plugin
# (docs.candide.dev/wallet/plugins/allowance), citing the identical two
# audits -- this is an adopted industry pattern, not an ARIA-only choice.
ALLOWANCE_MODULE_ADDRESS = "0xAA46724893dedD72658219405185Fb0Fc91e091C"

# Read-only slice of the real AllowanceModule ABI, fetched 17/08 from the
# contract's VERIFIED source on Blockscout (Robinhood Chain mainnet exposes
# the same CREATE2 address, `is_verified: true`, compiler v0.7.6+commit.
# 7338295f) and then cross-checked a second way: each 4-byte selector below
# was recomputed from its signature and confirmed PRESENT in the bytecode
# actually deployed on the testnet (a deliberately fake signature was
# confirmed ABSENT in the same pass, proving the check discriminates).
#
# Deliberately contains ONLY `view` functions -- this is a structural
# guardrail, not a convention: with no state-changing entry in the ABI,
# web3.py physically cannot build a `setAllowance`/`executeAllowanceTransfer`
# call from this contract object, so no future edit to this module can
# accidentally turn a read path into a spend path. Locked by
# ``test_allowance_module_abi_is_read_only``. Adding a write function here
# must be a deliberate, separately-reviewed decision (step 3+ of the plan in
# the module docstring), never a drive-by addition.
_ALLOWANCE_MODULE_VIEW_ABI = [
    {
        "name": "getTokenAllowance", "type": "function", "stateMutability": "view",
        "inputs": [
            {"name": "safe", "type": "address"},
            {"name": "delegate", "type": "address"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256[5]"}],
    },
    {
        "name": "getTokens", "type": "function", "stateMutability": "view",
        "inputs": [
            {"name": "safe", "type": "address"},
            {"name": "delegate", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "address[]"}],
    },
    {
        "name": "getDelegates", "type": "function", "stateMutability": "view",
        "inputs": [
            {"name": "safe", "type": "address"},
            {"name": "start", "type": "uint48"},
            {"name": "pageSize", "type": "uint8"},
        ],
        "outputs": [
            {"name": "results", "type": "address[]"},
            {"name": "next", "type": "uint48"},
        ],
    },
    {"name": "NAME", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
    {"name": "VERSION", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
]

# Index of the uint256[5] returned by `getTokenAllowance`, mirroring the
# contract's own `struct Allowance` field order.
#
# PROVEN EXPERIMENTALLY, not just read from the source (17/08): reading the
# source and seeing zeros come back from a live call proves nothing about
# field ORDER (zero == zero in any order -- a real trap caught mid-session).
# The decisive check used `eth_call` + `stateDiff` to inject a packed slot
# with five DISTINCT values (the struct packs to exactly one slot: 96+96+16+
# 32+16 = 256 bits, and `allowances` is the first non-constant variable, so
# slot 0 -> keccak(token ++ keccak(delegate ++ keccak(safe ++ 0)))). All five
# came back in exactly this order. Zero on-chain writes -- `stateDiff` is a
# simulation-only override.
#
# The same technique also PROVED the read-time reset documented in
# `read_allowance`: injecting spent=250 with a stale `lastResetMin` returns
# spent=0 (and a realigned `lastResetMin`), confirming `remaining` is
# trustworthy rather than stale. Re-run that probe before ever doubting
# these two facts again.
_ALLOWANCE_FIELDS = ("amount", "spent", "reset_time_min", "last_reset_min", "nonce")

# `getDelegates` is paginated; this bounds the walk so a malformed/hostile
# `next` pointer can never spin this loop forever (the contract is trusted
# here, but a read helper that can hang on-chain data is a real availability
# bug -- same fail-safe doctrine as the rest of this module).
_MAX_DELEGATE_PAGES = 20
_DELEGATE_PAGE_SIZE = 50


def _rpc_url() -> str:
    return (os.environ.get("ARIA_SAFE_ROBINHOOD_TESTNET_RPC_URL", "") or "").strip() or _DEFAULT_TESTNET_RPC_URL


def require_expected_chain(w3, allowed_chain_ids=frozenset({ROBINHOOD_TESTNET_CHAIN_ID})) -> None:
    """Shared fail-closed chain-id preflight -- the ONE place every write-path
    module in this dome (``safe_robinhood_deploy.py``, ``safe_robinhood_
    signer.py``, ``safe_robinhood_simulation.py``) now calls instead of each
    keeping its own copy (was a real duplication -- 3 near-identical private
    ``_require_testnet`` functions, cf. CLAUDE.md's architectural-coherence
    doctrine against restating a default that exists elsewhere).

    ``allowed_chain_ids`` defaults to testnet ONLY -- every caller in this
    dome still passes nothing today, so behavior is unchanged. It exists as a
    parameter (not a second hardcoded constant) so mainnet
    (``ROBINHOOD_MAINNET_CHAIN_ID``) can be opted into explicitly, ONE caller
    at a time, the day it's authorized -- never implicitly, never for every
    caller at once."""
    chain_id = w3.eth.chain_id
    if chain_id not in allowed_chain_ids:
        raise RuntimeError(
            f"refus: chaine {chain_id} pas dans les chaines autorisees {sorted(allowed_chain_ids)} "
            "-- preflight fail-closed"
        )


def verify_contracts_deployed(*, w3=None) -> dict:
    """Read-only sanity check: confirms both contracts above actually have
    bytecode on the configured (testnet-only) RPC right now, and that the RPC
    itself really answers for chain 46630 -- never assumed from a stale
    address list. Returns a dict, never raises on a network failure (mirrors
    ``sepolia_wallet.get_code``'s fail-safe pattern) -- a caller must check
    ``error`` explicitly rather than assume success from a truthy return."""
    from web3 import Web3

    if w3 is None:
        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))

    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001 -- network failure, never fabricate a result
        return {"error": f"RPC unreachable ({exc})", "chain_id_ok": None}

    chain_id_ok = chain_id == ROBINHOOD_TESTNET_CHAIN_ID
    result = {"error": None, "chain_id": chain_id, "chain_id_ok": chain_id_ok}

    for label, address in (
        ("safe_singleton", SAFE_SINGLETON_V141_ADDRESS),
        ("allowance_module", ALLOWANCE_MODULE_ADDRESS),
    ):
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(address))
            result[label] = {"address": address, "bytecode_len": len(code), "deployed": len(code) > 0}
        except Exception as exc:  # noqa: BLE001
            result[label] = {"address": address, "error": str(exc), "deployed": None}

    return result


def _allowance_module(w3=None):
    """Builds the read-only contract handle. Returns ``(contract, w3)``."""
    from web3 import Web3

    if w3 is None:
        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(ALLOWANCE_MODULE_ADDRESS),
        abi=_ALLOWANCE_MODULE_VIEW_ABI,
    )
    return contract, w3


def read_module_identity(*, w3=None) -> dict:
    """Read-only: asks the deployed contract what it actually IS (``NAME()``
    / ``VERSION()``), rather than trusting this module's own constants.

    Worth its own call because the address is a CREATE2 constant reused
    across chains: if a future network ever hosted a DIFFERENT contract at
    the same address, ``verify_contracts_deployed`` would still report
    "bytecode present" and look green. This is the check that would catch it.

    Also reports the RPC's real ``chain_id``. KNOWN RESIDUAL, stated rather
    than hidden: the ``read_*`` helpers below do NOT re-verify the network on
    every call (that would double the RPC cost of every read), so a
    misconfigured ``ARIA_SAFE_ROBINHOOD_TESTNET_RPC_URL`` pointing at mainnet
    would be read without complaint. Harmless while everything here is
    read-only (no funds can move), but it MUST become a hard pre-flight check
    before step 3 (signing) -- noted in the module docstring's plan. Call this
    function first when a session wants certainty about which chain it reads.

    Fail-safe: returns ``error`` instead of raising."""
    try:
        contract, resolved_w3 = _allowance_module(w3)
        name = contract.functions.NAME().call()
        version = contract.functions.VERSION().call()
    except Exception as exc:  # noqa: BLE001 -- network/ABI failure, never fabricate
        return {"error": f"module identity unreadable ({exc})", "name": None, "version": None}

    # Secondary, best-effort: a chain-id read must never turn a successful
    # identity check into a failure -- reported as None if unavailable.
    try:
        chain_id = int(resolved_w3.eth.chain_id)
    except Exception:  # noqa: BLE001
        chain_id = None

    return {
        "error": None,
        "name": name,
        "version": version,
        "chain_id": chain_id,
        "on_expected_testnet": chain_id == ROBINHOOD_TESTNET_CHAIN_ID if chain_id else None,
        # Confirmed live on the testnet 17/08 -- a mismatch here means the
        # contract at this address changed and every assumption below is stale.
        "matches_expected": name == "Allowance Module" and version == "0.1.1",
    }


def read_allowance(safe: str, delegate: str, token: str, *, w3=None) -> dict:
    """Read-only: the live on-chain spending allowance granted by ``safe`` to
    ``delegate`` for ``token``. THE core read of this whole architecture --
    it answers "how much can the agent key still spend right now", which is
    the bound the operator ultimately relies on.

    ``remaining`` is safe to trust, and that is NOT obvious -- it was
    verified in the contract's real source before being exposed here:
    ``getAllowance`` (private, called by ``getTokenAllowance``) applies the
    periodic reset AT READ TIME (`if resetTimeMin > 0 && lastResetMin <=
    currentMin - resetTimeMin { spent = 0 }`), so the ``spent`` returned is
    already reset-corrected -- a naive ``amount - spent`` does NOT
    under-report an allowance whose period has rolled over. The contract also
    enforces ``newSpent <= amount`` on every transfer, so ``remaining`` can
    never come back negative. Both facts read from the verified source, not
    assumed.

    Amounts are RAW token units (the module is token-decimals agnostic) --
    deliberately not converted here, since converting would require trusting
    a separate `decimals()` call; the caller that knows the token converts.

    ⚠ NEVER treat ``remaining`` as authorisation to spend. It is a SNAPSHOT
    at read time: between this read and any later transfer, another delegate
    (or the same key racing itself across two cycles) can consume the same
    allowance, and a period rollover can change it too. The only authority
    that actually bounds a spend is the contract itself, which re-checks
    ``newSpent <= amount`` atomically at execution and reverts otherwise.
    This function is for OBSERVING and reporting -- treating its output as a
    green light would reintroduce, off-chain, exactly the check-then-act race
    the on-chain module exists to eliminate."""
    from web3 import Web3

    try:
        contract, _ = _allowance_module(w3)
        raw = contract.functions.getTokenAllowance(
            Web3.to_checksum_address(safe),
            Web3.to_checksum_address(delegate),
            Web3.to_checksum_address(token),
        ).call()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"allowance unreadable ({exc})", "amount": None, "remaining": None}

    values = {field: int(raw[i]) for i, field in enumerate(_ALLOWANCE_FIELDS)}
    return {
        "error": None,
        "safe": safe,
        "delegate": delegate,
        "token": token,
        **values,
        "remaining": values["amount"] - values["spent"],
        # A never-configured allowance reads as all-zeros, which is NOT the
        # same as a configured allowance that is fully spent -- surfaced
        # explicitly so a caller never mistakes "no allowance exists" for
        # "allowance exhausted" (opposite operator actions).
        "configured": values["amount"] > 0 or values["reset_time_min"] > 0,
        # TWO REGIMES, both valid, and the distinction matters for step 2's
        # design decision: `reset_time_min == 0` is a ONE-SHOT allowance that
        # never renews (the contract's read-time reset is gated on
        # `resetTimeMin > 0`, so `spent` accumulates forever); anything else
        # renews every N minutes. v0.1.1's `require(resetTimeMin > 0)` only
        # fires when `resetBaseMin > 0`, so a one-shot allowance IS still
        # reachable -- verified in the deployed source, not assumed. A
        # trading agent almost certainly wants the PERIODIC regime (a daily
        # cap that refills) rather than a one-shot budget that silently dries
        # up; that choice is the operator's, at step 2.
        "renews": values["reset_time_min"] > 0,
    }


def read_allowance_tokens(safe: str, delegate: str, *, w3=None) -> dict:
    """Read-only: which tokens ``delegate`` holds an allowance for on
    ``safe``. Fail-safe, same contract as the other readers."""
    from web3 import Web3

    try:
        contract, _ = _allowance_module(w3)
        tokens = contract.functions.getTokens(
            Web3.to_checksum_address(safe),
            Web3.to_checksum_address(delegate),
        ).call()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"token list unreadable ({exc})", "tokens": None}

    return {"error": None, "tokens": [str(t) for t in tokens]}


def read_delegates(safe: str, *, w3=None) -> dict:
    """Read-only: every delegate (agent key) currently authorised on ``safe``.

    Walks the contract's linked-list pagination. Bounded by
    ``_MAX_DELEGATE_PAGES``: if the walk is cut short, ``truncated`` is True
    -- never silently returns a partial list as if it were complete (a
    partial delegate list would badly mislead a security review)."""
    from web3 import Web3

    delegates: list[str] = []
    start = 0
    try:
        contract, _ = _allowance_module(w3)
        safe_addr = Web3.to_checksum_address(safe)
        for _ in range(_MAX_DELEGATE_PAGES):
            page, next_start = contract.functions.getDelegates(
                safe_addr, start, _DELEGATE_PAGE_SIZE
            ).call()
            delegates.extend(str(d) for d in page)
            if not next_start:
                return {"error": None, "delegates": delegates, "truncated": False}
            start = next_start
    except Exception as exc:  # noqa: BLE001
        return {"error": f"delegates unreadable ({exc})", "delegates": None, "truncated": None}

    return {"error": None, "delegates": delegates, "truncated": True}
