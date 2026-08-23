"""Robinhood Chain swap router -- ISOLATED CONSTRUCTION SITE, NEVER WIRED (23/08).

Built ahead of a governance decision that has NOT been made yet: the operator
asked for real trading on Robinhood Chain, and the absolute rules in
``CLAUDE.md`` ("Mecanismes de trading automatique actifs") name exactly three
real-capital exceptions -- fictitious paper-trading, the Coinbase CDP pilot on
Base ($10-25), and the Solana pilot ($0.10) -- Robinhood is not among them.
Adding that exception requires an explicit operator decision, never something
a coding session infers from insistence. The agreed compromise (23/08): build
the missing technical piece now, in strict isolation, so it is ready and
tested the day the operator makes that call -- not to make anything trade
today.

**Isolation contract, enforced by review not by a runtime gate:**
  - Nothing in this module is reachable from ``heartbeat.py``,
    ``paper_trader.py``, or any other production/scheduled path. Grep this
    repo for ``robinhood_swap_router`` outside ``tests/`` and this docstring
    -- the only hits should be this file and its test file.
  - No ``ARIA_*_ENABLED`` gate is defined here on purpose. A gate would imply
    this module is one flag away from production; it is not supposed to be
    reachable at all yet. The only thing that can ever call the real-signing
    function below is a direct script or a test.
  - ``chain_id`` is checked against ``ROBINHOOD_TESTNET_CHAIN_ID`` (imported,
    never restated, from ``safe_robinhood_wallet`` -- same constant already
    locked for every other Robinhood Chain mechanism in this repo) before any
    transaction is ever built or signed. There is no mainnet code path here.

**Diligence result (23/08 session, "depth proportional to the stakes" --
touches a future real-capital mechanism), see the HANDOFF entry this ships
with for the full trail:**
  - Uniswap v2/v3/v4 and UniswapX are confirmed genuinely deployed on
    Robinhood Chain -- but on **MAINNET only** (chain id 4663), per Uniswap's
    own announcement (blog.uniswap.org/robinhood-chain-is-live) and its
    official per-chain deployment-address page
    (developers.uniswap.org, v3 deployments). "Uniswap deployed a dedicated
    AMM as a primary public liquidity layer on day one" -- this is a real,
    documented DEX, not a guess.
  - **No testnet (chain id 46630) deployment is documented anywhere found**
    -- the official deployment-address page explicitly covers mainnet only.
    This module's own hard governance bound (testnet-only, see above) means
    those mainnet addresses could never legally be used here anyway, even if
    trusted at face value.
  - The mainnet addresses this session did find came back through a
    web-fetch summarization step, not a byte-for-byte source render -- a
    single mistranscribed hex character is exactly the failure mode this
    project's own "never an assumed address in a signed transaction, verify
    via eth_getCode" doctrine (``sepolia_wallet.py``, ``safe_robinhood_
    wallet.py``) exists to catch. This session had no live RPC reachability
    to Robinhood Chain to run that verification (same limitation already
    hit and documented once before, ``sepolia_wallet.py``'s own docstring).
  - Conclusion, stated rather than papered over: **no router/factory/quoter
    address is hardcoded anywhere in this module.** Every address is a
    required, explicit configuration value (``RobinhoodSwapConfig``) --
    fictitious in every automated test, to be filled in with a REAL,
    ``eth_getCode``-verified testnet address only once one is confirmed to
    exist (or, on an explicit later operator decision, a mainnet address
    once this module's testnet-only bound is itself formally revisited).

**Mechanics reused, not reinvented:** the same Uniswap V3 ``exactInputSingle``
ABI fragment already proven working end-to-end in
``sepolia_wallet.send_test_swap_transaction`` (a REAL signed swap on Base
Sepolia) -- this is the stable, standard Uniswap V3 router interface, which
does not itself need chain-specific verification (only the deployment
*address* does). The wrap-native -> approve -> swap transaction sequence
mirrors that same proven function.

**Future integration point (not wired):** mirrors ``safe_robinhood_signer.
send_allowance_transfer``, which is injected as ``homemade_agent_wallet.
attempt_transfer``'s ``send_fn``. ``execute_bounded_swap`` below is written
to the same ``async def (*, amount, **kwargs) -> dict`` shape so that, on the
day this is wired, it can be injected as a ``send_fn`` the exact same way --
no interface rework anticipated, only the wiring itself, which is
deliberately not done here.
"""
from __future__ import annotations

from dataclasses import dataclass

from aria_core.onchain.safe_robinhood_wallet import ROBINHOOD_TESTNET_CHAIN_ID

# Project-wide absolute rule ("slippage jamais au-dela de 10%, toujours
# explicite, jamais la valeur par defaut d'un outil de trade") -- enforced
# structurally here: no default slippage exists anywhere in this module,
# and no configured value above this ceiling can ever be constructed.
MAX_SLIPPAGE_BPS = 1_000  # 10.00%, expressed in basis points (1 bps = 0.01%)

# Mechanical safety ceiling on the amount this module will ever build a
# transaction for, mirroring ``sepolia_wallet.MAX_TEST_SWAP_WEI`` -- a
# hardcoded value, not a gate/env var (this module isn't meant to be
# reachable in the first place; this is a second, independent bound in case
# it is ever called directly with a mistaken amount).
MAX_TEST_SWAP_WEI = 2 * 10**15  # ~0.002 ETH-equivalent, same order as sepolia_wallet

# Standard WETH9-shaped ABI fragment (deposit/approve) -- identical to the
# one already proven live in ``sepolia_wallet.py``; the interface is a
# stable ERC-20/WETH standard, not something specific to Robinhood Chain.
_WETH_ABI = [
    {
        "inputs": [], "name": "deposit", "outputs": [],
        "stateMutability": "payable", "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable", "type": "function",
    },
]

# Uniswap V3 SwapRouter ``exactInputSingle`` -- the same ABI fragment
# already proven byte-for-byte against a real deployed router in
# ``sepolia_wallet.py``. Deliberately reused rather than restated from
# scratch: this is the stable ISwapRouter interface, unrelated to which
# chain the router is deployed on.
_SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params", "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable", "type": "function",
    }
]


class RobinhoodSwapConfigError(RuntimeError):
    """Raised on any invalid/missing swap configuration -- never a silent
    default, matching the fail-closed doctrine applied to every other
    real-capital-adjacent module in this repo."""


@dataclass(frozen=True)
class RobinhoodSwapConfig:
    """Explicit, fully-parameterized swap configuration -- see the module
    docstring's diligence result for why no address here ever has a
    hardcoded default. Constructing an instance validates every bound
    immediately (fail fast, not at signing time)."""

    router_address: str
    token_in: str
    token_out: str
    fee_tier: int
    slippage_bps: int
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID

    def __post_init__(self) -> None:
        if self.chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
            raise RobinhoodSwapConfigError(
                f"refuse : chain_id {self.chain_id} != testnet Robinhood "
                f"({ROBINHOOD_TESTNET_CHAIN_ID}) -- ce module n'existe que "
                "pour le testnet, aucun chemin mainnet n'est implemente ici"
            )
        if not self.router_address or not self.token_in or not self.token_out:
            raise RobinhoodSwapConfigError(
                "router_address/token_in/token_out doivent etre fournis explicitement "
                "-- aucune adresse par defaut n'existe dans ce module (cf. diligence "
                "23/08 : aucun DEX confirme deploye sur le testnet Robinhood)"
            )
        if not (0 < self.slippage_bps <= MAX_SLIPPAGE_BPS):
            raise RobinhoodSwapConfigError(
                f"slippage {self.slippage_bps} bps hors bornes (0, {MAX_SLIPPAGE_BPS}] "
                "-- regle absolue du projet : jamais >10%, toujours explicite"
            )
        if self.fee_tier <= 0:
            raise RobinhoodSwapConfigError(f"fee_tier invalide : {self.fee_tier}")


def compute_min_amount_out(quoted_amount_out: int, slippage_bps: int) -> int:
    """``amountOutMinimum`` for ``exactInputSingle`` -- floor division, so the
    bound is always at least as strict as the requested slippage (never
    rounds in the trader's favor). Raises on an out-of-bounds slippage
    rather than silently clamping it, so a caller can never accidentally
    construct a swap with no real floor."""
    if not (0 < slippage_bps <= MAX_SLIPPAGE_BPS):
        raise RobinhoodSwapConfigError(
            f"slippage {slippage_bps} bps hors bornes (0, {MAX_SLIPPAGE_BPS}]"
        )
    if quoted_amount_out < 0:
        raise RobinhoodSwapConfigError(f"quoted_amount_out negatif : {quoted_amount_out}")
    return (quoted_amount_out * (10_000 - slippage_bps)) // 10_000


def _require_testnet(w3) -> None:
    """Fail-closed chain preflight, reading the LIVE chain id from the
    injected RPC client -- same pattern as ``safe_robinhood_signer.
    _require_testnet``. An RPC repointed at mainnet by config drift must
    raise here, never silently build/sign a mainnet transaction."""
    chain_id = w3.eth.chain_id
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise RuntimeError(
            f"refuse : chaine {chain_id} != testnet Robinhood "
            f"({ROBINHOOD_TESTNET_CHAIN_ID}) -- ce module ne signe jamais "
            "en dehors du testnet"
        )


def build_swap_transaction(
    *,
    config: RobinhoodSwapConfig,
    amount_in: int,
    quoted_amount_out: int,
    recipient: str,
    nonce: int,
    w3,
) -> dict:
    """Builds (never signs, never sends) the ``exactInputSingle`` transaction
    dict. Pure construction, so it can be exercised and checked byte-for-byte
    in a test without needing a full signing/broadcast fake chain."""
    if amount_in <= 0 or amount_in > MAX_TEST_SWAP_WEI:
        raise RobinhoodSwapConfigError(
            f"montant refuse : {amount_in} hors bornes (0, {MAX_TEST_SWAP_WEI}] "
            "-- plafond de securite mecanique, pas un montant de trading"
        )

    min_amount_out = compute_min_amount_out(quoted_amount_out, config.slippage_bps)

    router_cs = w3.to_checksum_address(config.router_address)
    token_in_cs = w3.to_checksum_address(config.token_in)
    token_out_cs = w3.to_checksum_address(config.token_out)
    recipient_cs = w3.to_checksum_address(recipient)

    swap_router = w3.eth.contract(address=router_cs, abi=_SWAP_ROUTER_ABI)
    swap_params = (
        token_in_cs, token_out_cs, config.fee_tier, recipient_cs,
        amount_in, min_amount_out, 0,
    )
    return swap_router.functions.exactInputSingle(swap_params).build_transaction({
        "from": recipient_cs,
        "nonce": nonce,
        "chainId": config.chain_id,
    })


async def execute_bounded_swap(
    *,
    config: RobinhoodSwapConfig,
    amount: int,
    quoted_amount_out: int,
    account,
    w3,
    wrap_native: bool = False,
    wait_for_receipt: bool = True,
) -> dict:
    """NOT WIRED to any real caller -- isolation chantier, cf. CLAUDE.md
    governance gate on Robinhood real trading (23/08). Reachable only from a
    direct script or a test, never from ``heartbeat.py``/``paper_trader.py``/
    any production path.

    Wrap native (optional) -> approve -> ``exactInputSingle``, mirroring the
    three-real-signed-transactions sequence already proven live in
    ``sepolia_wallet.send_test_swap_transaction`` -- the mechanism, not the
    market decision, is what this proves.

    Declared ``async`` to match the injectable ``send_fn`` shape used across
    the dome (``homemade_agent_wallet.SendFn``, ``agent_wallet_pilot.py``'s
    ``swap_fn``/``transfer_fn``) -- purely an interface-consistency choice
    for the day this is wired; web3.py's HTTP provider is synchronous, so
    this never actually yields control mid-call, same documented rationale
    as ``safe_robinhood_signer.send_allowance_transfer``.

    Never raises past config validation; a network/send failure is reported
    as ``{"error": ..., "tx_hash": None}`` so a future caller (a guardrail
    wrapper, not built here) can log and classify it rather than crash --
    same contract as ``safe_robinhood_signer.send_allowance_transfer``.

    ``w3`` is REQUIRED (no default RPC constant lives in this module -- cf.
    the diligence result above: no address is confirmed yet for a default
    to usefully point at). A caller wiring this for real needs
    ``safe_robinhood_wallet._rpc_url()``'s ``ARIA_SAFE_ROBINHOOD_TESTNET_
    RPC_URL``, already used elsewhere in this repo for this exact chain.
    """
    _require_testnet(w3)

    if amount <= 0 or amount > MAX_TEST_SWAP_WEI:
        return {
            "error": (
                f"montant refuse : {amount} hors bornes (0, {MAX_TEST_SWAP_WEI}]"
            ),
            "tx_hash": None,
        }

    def _hex(tx_hash) -> str:
        # Same normalization as ``safe_robinhood_signer.send_allowance_transfer``
        # -- web3.py versions differ on whether ``.hex()`` already carries the
        # "0x" prefix, so this is checked rather than assumed.
        h = tx_hash.hex()
        return h if h.startswith("0x") else "0x" + h

    def _sign_and_send(built_tx):
        signed = account.sign_transaction(built_tx)
        return w3.eth.send_raw_transaction(signed.raw_transaction)

    try:
        nonce = w3.eth.get_transaction_count(account.address)
        token_in_cs = w3.to_checksum_address(config.token_in)
        router_cs = w3.to_checksum_address(config.router_address)

        if wrap_native:
            weth = w3.eth.contract(address=token_in_cs, abi=_WETH_ABI)
            deposit_tx = weth.functions.deposit().build_transaction({
                "from": account.address, "value": amount,
                "nonce": nonce, "chainId": config.chain_id,
            })
            deposit_hash_raw = _sign_and_send(deposit_tx)
            deposit_hash = _hex(deposit_hash_raw)
            nonce += 1
        else:
            deposit_hash = None

        weth = w3.eth.contract(address=token_in_cs, abi=_WETH_ABI)
        approve_tx = weth.functions.approve(router_cs, amount).build_transaction({
            "from": account.address, "nonce": nonce, "chainId": config.chain_id,
        })
        approve_hash = _hex(_sign_and_send(approve_tx))
        nonce += 1

        swap_tx = build_swap_transaction(
            config=config, amount_in=amount, quoted_amount_out=quoted_amount_out,
            recipient=account.address, nonce=nonce, w3=w3,
        )
        swap_hash_raw = _sign_and_send(swap_tx)
        swap_hash = _hex(swap_hash_raw)
    except Exception as exc:  # noqa: BLE001 -- network/send failure, never fabricate a result
        return {"error": str(exc), "tx_hash": None}

    result = {
        "error": None, "deposit_tx": deposit_hash, "approve_tx": approve_hash,
        "swap_tx": swap_hash, "tx_hash": swap_hash, "status": None,
    }
    if wait_for_receipt:
        try:
            receipt = w3.eth.wait_for_transaction_receipt(swap_hash_raw, timeout=60)
            result["status"] = "ok" if receipt.status == 1 else "reverted"
        except Exception as exc:  # noqa: BLE001 -- receipt lookup failure; tx may still be pending
            result["status"] = f"unknown ({exc})"
    return result
