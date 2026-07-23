"""ARIA's Sepolia wallet — the ONLY documented exception to "private key never on the server".

``send_test_swap_transaction`` (added 07/09, explicit operator decision: "real
swap on Sepolia, test asset") executes a REAL Uniswap V3 swap (wrap WETH ->
approve -> exactInputSingle, three real signed transactions) but on a
configured TEST pair (``ARIA_SEPOLIA_SWAP_TOKEN_OUT``), never on the candidate
token ARIA is actually analyzing (which doesn't exist on this testnet, a
different chain from Base mainnet). Bounded goal: prove that the swap
signing/broadcast/confirmation mechanism actually works (gas, slippage, nonce,
RPC failures) — NOT validate a market strategy. Router/output-token address
not provided by default: must be verified on-chain (bytecode + real liquidity)
before arming ``ARIA_SEPOLIA_SWAP_ENABLED``; this verification could not be
done from this session (no direct RPC access in this environment — see HANDOFF).

Unlike ``onchain/anchor.py`` (preparation only, 100% local signing by the
operator) and ``services/x402.py`` (no key, ever), this module HOLDS a private
key on the server and REALLY signs transactions. Explicit operator decision
(07/08): pre-mainnet rehearsal — anticipate and solve problems (RPC, gas,
nonce, broadcast failures) on a network where ETH is worthless, before ever
considering the same mechanics on real funds.

Guardrails:
  - **Chain ID locked** to ``SEPOLIA_CHAIN_ID`` (84532) — any request for a
    different chain_id is refused before even touching the key (fail-closed).
    Structurally prevents this code from ever signing on mainnet by accident.
  - **Gated OFF by default** (``ARIA_SEPOLIA_WALLET_ENABLED``): no key is even
    read without this flag.
  - **Never called directly**: only from ``wallet_guard.resolve_spend``,
    reachable only after a real Telegram click (same guardrail as
    ``client_fund_job``/``trade_tokens``).
  - The key (``ARIA_SEPOLIA_PRIVATE_KEY``) lives only in the VPS ``.env`` —
    never in the repo, never logged, never returned by any function in this
    module.
"""
from __future__ import annotations

import os

SEPOLIA_CHAIN_ID = 84532
_DEFAULT_RPC_URL = "https://sepolia.base.org"

# Minimal ABI fragment — only the function used by this module (AriaLedger.anchor,
# no value transfer, see contracts/AriaLedger.sol).
_ANCHOR_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "root", "type": "bytes32"}],
        "name": "anchor",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Minimal ABI fragments for the test swap — only the functions called
# (standard WETH9: deposit/approve; standard Uniswap V3 SwapRouter02: exactInputSingle).
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

MAX_TEST_SWAP_WEI = 2 * 10**15  # hard cap ~0.002 testnet ETH (no real value) per swap


def sepolia_swap_enabled() -> bool:
    """Additive gate dedicated to the test swap — on top of sepolia_wallet_enabled,
    never active alone. The wallet can anchor decisions without ever swapping."""
    if not sepolia_wallet_enabled():
        return False
    return os.environ.get("ARIA_SEPOLIA_SWAP_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def swap_router_address() -> str:
    return (os.environ.get("ARIA_SEPOLIA_SWAP_ROUTER", "") or "").strip()


def swap_token_in() -> str:
    """Pre-deployed OP-stack WETH — same address on all OP-stack chains
    (Base, Base Sepolia included), no need for per-environment verification."""
    return (
        os.environ.get("ARIA_SEPOLIA_SWAP_TOKEN_IN", "") or ""
    ).strip() or "0x4200000000000000000000000000000000000006"


def swap_token_out() -> str:
    return (os.environ.get("ARIA_SEPOLIA_SWAP_TOKEN_OUT", "") or "").strip()


def swap_fee_tier() -> int:
    return int(os.environ.get("ARIA_SEPOLIA_SWAP_FEE_TIER", "3000") or 3000)


def sepolia_wallet_enabled() -> bool:
    """Seam gated OFF by default. No key read, no RPC connection without this flag."""
    return os.environ.get("ARIA_SEPOLIA_WALLET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _private_key() -> str:
    return (os.environ.get("ARIA_SEPOLIA_PRIVATE_KEY", "") or "").strip()


def _rpc_url() -> str:
    return (os.environ.get("ARIA_SEPOLIA_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL


def _account(*, account_cls=None):
    """Account derived from the private key. ``account_cls`` injectable (offline tests)."""
    if not sepolia_wallet_enabled():
        return None
    key = _private_key()
    if not key:
        return None
    if account_cls is None:
        from eth_account import Account as account_cls  # noqa: N813
    return account_cls.from_key(key)


def get_address(*, account_cls=None) -> str | None:
    """Public address of ARIA's Sepolia wallet — safe to expose (never the key itself)."""
    account = _account(account_cls=account_cls)
    return account.address if account else None


def get_balance_eth(*, w3=None, account_cls=None) -> float | None:
    """Sepolia ETH balance (no real value). None if not configured/unavailable —
    never an exception bubbling up for a simple balance read."""
    address = get_address(account_cls=account_cls)
    if not address:
        return None
    try:
        if w3 is None:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))
        wei = w3.eth.get_balance(w3.to_checksum_address(address))
        return float(w3.from_wei(wei, "ether"))
    except Exception:
        return None


def get_code(address: str, *, w3=None) -> dict | None:
    """Read-only RPC call (``eth_getCode``) — no key, no signing, doesn't even depend
    on ARIA's wallet. Used to verify that a contract (router, pool) REALLY exists on
    Base Sepolia before configuring a real swap on it (``ARIA_SEPOLIA_SWAP_ROUTER`` /
    ``_TOKEN_OUT``) — never an unverified address in a signed transaction. None if the
    read fails (RPC down, invalid address) — never an exception bubbling up."""
    try:
        if w3 is None:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 15}))
        code = w3.eth.get_code(w3.to_checksum_address(address))
        code_hex = code.hex() if hasattr(code, "hex") else str(code)
        return {
            "address": address,
            "has_code": len(code) > 0,
            "code_length_bytes": len(code),
            "code_preview": ("0x" + code_hex.removeprefix("0x"))[:66],
        }
    except Exception:
        return None


def send_anchor_transaction(
    *, contract: str, root: str, chain_id: int, w3=None, account_cls=None,
) -> str:
    """Signs and broadcasts ``anchor(bytes32 root)`` on Sepolia ONLY.

    Raises (never silently returns) if the seam is OFF, if ``chain_id`` isn't
    Sepolia, or if the broadcast fails — unlike the rest of the onchain
    guardrails (preparation only, graceful degradation), a really-signed
    transaction must always surface a clear error to the operator rather than
    disappear.
    """
    if not sepolia_wallet_enabled():
        raise RuntimeError("wallet Sepolia désactivé (ARIA_SEPOLIA_WALLET_ENABLED)")
    if int(chain_id) != SEPOLIA_CHAIN_ID:
        raise RuntimeError(
            f"refusé : chain_id {chain_id} != Sepolia ({SEPOLIA_CHAIN_ID}) — "
            "ce wallet ne signe jamais en dehors du testnet"
        )
    account = _account(account_cls=account_cls)
    if account is None:
        raise RuntimeError("ARIA_SEPOLIA_PRIVATE_KEY absente — rien à signer")

    if w3 is None:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))

    root_hex = root[2:] if root.startswith("0x") else root
    root_bytes = bytes.fromhex(root_hex)

    ledger = w3.eth.contract(address=w3.to_checksum_address(contract), abi=_ANCHOR_ABI)
    tx = ledger.functions.anchor(root_bytes).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": SEPOLIA_CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


def send_test_swap_transaction(
    *,
    amount_in_wei: int,
    chain_id: int,
    router: str | None = None,
    token_in: str | None = None,
    token_out: str | None = None,
    fee: int | None = None,
    w3=None,
    account_cls=None,
) -> dict:
    """Wrap WETH -> approve -> exactInputSingle: three really-signed transactions
    broadcast on Sepolia ONLY, on the configured TEST pair — never the candidate
    token ARIA is actually analyzing (doesn't exist on this testnet). Tests the
    execution mechanism (signing, gas, nonce, confirmation), not a market decision.

    Raises (never a silent degradation, like ``send_anchor_transaction``) if the
    seam is OFF, off Sepolia, the amount exceeds ``MAX_TEST_SWAP_WEI``, or if the
    router/output token aren't configured — no invented default value for an
    unverified contract.
    """
    if not sepolia_swap_enabled():
        raise RuntimeError("swap de test Sepolia désactivé (ARIA_SEPOLIA_SWAP_ENABLED)")
    if int(chain_id) != SEPOLIA_CHAIN_ID:
        raise RuntimeError(
            f"refusé : chain_id {chain_id} != Sepolia ({SEPOLIA_CHAIN_ID}) — "
            "ce wallet ne signe jamais en dehors du testnet"
        )
    if amount_in_wei <= 0 or amount_in_wei > MAX_TEST_SWAP_WEI:
        raise RuntimeError(
            f"montant refusé : {amount_in_wei} wei hors bornes (0, {MAX_TEST_SWAP_WEI}] — "
            "plafond de sécurité mécanique, pas un montant de trading"
        )

    router = (router or swap_router_address()).strip()
    token_in = (token_in or swap_token_in()).strip()
    token_out = (token_out or swap_token_out()).strip()
    fee = fee if fee is not None else swap_fee_tier()
    if not router or not token_out:
        raise RuntimeError(
            "routeur ou token de sortie non configurés (ARIA_SEPOLIA_SWAP_ROUTER / "
            "ARIA_SEPOLIA_SWAP_TOKEN_OUT) — vérification on-chain requise avant swap réel"
        )

    account = _account(account_cls=account_cls)
    if account is None:
        raise RuntimeError("ARIA_SEPOLIA_PRIVATE_KEY absente — rien à signer")

    if w3 is None:
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 20}))

    router_cs = w3.to_checksum_address(router)
    token_in_cs = w3.to_checksum_address(token_in)
    token_out_cs = w3.to_checksum_address(token_out)

    weth = w3.eth.contract(address=token_in_cs, abi=_WETH_ABI)
    swap_router = w3.eth.contract(address=router_cs, abi=_SWAP_ROUTER_ABI)

    def _sign_and_send(built_tx) -> str:
        signed = account.sign_transaction(built_tx)
        return w3.eth.send_raw_transaction(signed.raw_transaction).hex()

    nonce = w3.eth.get_transaction_count(account.address)

    deposit_tx = weth.functions.deposit().build_transaction({
        "from": account.address, "value": amount_in_wei,
        "nonce": nonce, "chainId": SEPOLIA_CHAIN_ID,
    })
    deposit_hash = _sign_and_send(deposit_tx)

    approve_tx = weth.functions.approve(router_cs, amount_in_wei).build_transaction({
        "from": account.address, "nonce": nonce + 1, "chainId": SEPOLIA_CHAIN_ID,
    })
    approve_hash = _sign_and_send(approve_tx)

    swap_params = (
        token_in_cs, token_out_cs, fee, account.address,
        amount_in_wei, 0, 0,
    )
    swap_tx = swap_router.functions.exactInputSingle(swap_params).build_transaction({
        "from": account.address, "nonce": nonce + 2, "chainId": SEPOLIA_CHAIN_ID,
    })
    swap_hash = _sign_and_send(swap_tx)

    return {"deposit_tx": deposit_hash, "approve_tx": approve_hash, "swap_tx": swap_hash}
