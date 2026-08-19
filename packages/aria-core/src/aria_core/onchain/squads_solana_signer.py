"""ARIA's homemade agent wallet, Solana leg — REAL signing module (19/08).

Promotes the one-off script logic already proven live on devnet (both 18/08
entries in ``docs/HANDOFF_AGENT_WALLET.md`` — "Solana leg reaches PARITY..."
and the anchorpy-IDL milestone before it) into committed, tested code.
Mirrors ``onchain/safe_robinhood_signer.py``'s own promotion exactly: same
"reject locally on a fresh on-chain read, right before sending, never a
cached figure" doctrine, same "``send_fn`` injected into the guardrail
wrapper" contract (``homemade_agent_wallet.attempt_transfer``), same "no
secret ever hardcoded/returned/logged" rule.

Deliberately does NOT use anchorpy at the instruction-BUILDING layer — the
two real anchorpy 0.21 bugs already documented in this file's sibling
module's history (a ``solders.Pubkey`` nested inside a generated Borsh-arg
dataclass crashing ``dataclasses.asdict``/``copy.deepcopy``, and an IDL whose
``isOptional`` account metadata anchorpy's own instruction builder does not
honour) live inside anchorpy's *generated client* code path, which this
module never calls. Instead, everything needed to build the instruction byte
for byte is taken directly from Squads v4's real fetched on-chain IDL
(``squads_solana_wallet.fetch_program_idl``, re-verified this session —
``squads_multisig_program`` v2.1.0) and the real ``Squads-Protocol/v4``
GitHub source (``state/seeds.rs``, ``instructions/spending_limit_use.rs``,
``state/spending_limit.rs``, fetched via ``gh api`` this session, never from
memory) — re-verify against those sources again before trusting this file
blindly in a future session. Raw solders primitives (``Pubkey``/
``Instruction``/``Transaction``) build the real transaction; every read goes
through plain JSON-RPC (``httpx``, injectable ``client`` — same DI pattern as
``squads_solana_wallet.verify_program_deployed``) rather than anchorpy's own
account fetcher, keeping this module's only external write dependency the
officially-maintained ``solders``/``solana-py`` libraries.

PDA derivation (seeds re-derived this session and matched byte for byte
against the real already-existing on-chain multisig/spending-limit/vault
from the 18/08 one-off milestone — never guessed):
  multisig       = [b"multisig", b"multisig", multisig_create_key]
  spending_limit = [b"multisig", multisig_pda, b"spending_limit", spending_limit_create_key]
  vault          = [b"multisig", multisig_pda, b"vault", bytes([vault_index])]

``SpendingLimit`` account layout (``state/spending_limit.rs``, decoded
manually since this module deliberately never constructs an anchorpy
``Program``/account-fetcher for a write-adjacent read): Anchor's 8-byte
account discriminator, then ``multisig``:32, ``create_key``:32,
``vault_index``:1, ``mint``:32, ``amount``:8 (u64 LE), ``period``:1,
``remaining_amount``:8 (u64 LE), ``last_reset``:8 (i64 LE), ``bump``:1,
followed by the ``members``/``destinations`` vectors this module never needs
to parse. This session's spending limit is always ``Period::OneTime``
(operator decision, 17/08 — the same one-shot-never-refills regime as the
EVM leg's AllowanceModule cap), so a freshly-read ``remaining_amount`` is
always the authoritative figure with no reset-window staleness to reason
about; a future PERIODIC spending limit would need reset-aware logic this
module does not implement (mirrors the EVM leg's own documented limits).

No private key is ever hardcoded, accepted as a literal parameter, logged,
or returned — ``_load_delegate_key`` reads the delegate's native
``solana-keygen``-format JSON (a bare 64-int secret-key array — the exact
format already on disk at the real, already-FUNDED devnet delegate key path,
reused rather than regenerated) from a path the caller supplies explicitly;
an unset/unreadable path fails closed via ``DelegateKeyError``.

This module has NO gate of its own and must never be called directly from a
production/heartbeat path — bounding when/how much it may spend is the job
of ``homemade_agent_wallet.attempt_transfer`` (``chain="solana"``), exactly
like the EVM leg. ``send_spending_limit_transfer`` is meant to be injected as
that wrapper's ``send_fn``, never called on its own from anywhere that isn't
itself gated + kill-switch-checked.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

from aria_core.onchain.squads_solana_wallet import SQUADS_V4_PROGRAM_ID
from aria_core.onchain.squads_solana_wallet import _rpc_url as _devnet_rpc_url

logger = logging.getLogger(__name__)

# Anchor's global instruction discriminator convention: first 8 bytes of
# sha256("global:<snake_case_instruction_name>") — no discriminator is
# embedded in this program's (pre-0.30-style) IDL, so it is computed here
# exactly as the real anchor-ts client does, not guessed.
_SPENDING_LIMIT_USE_DISCRIMINATOR = hashlib.sha256(b"global:spending_limit_use").digest()[:8]

# SOL transfers through `spending_limit_use` are hard-required by the
# program itself to declare 9 decimals (`require!(args.decimals == 9, ...)`,
# `spending_limit_use.rs`) — a sanity check against a wrong order of
# magnitude, not a configurable value for the native-SOL case this file
# uses exclusively.
SOL_DECIMALS = 9


class DelegateKeyError(RuntimeError):
    """Raised on any problem loading the delegate key — never falls back to a
    default or a degraded mode, matching the fail-closed doctrine applied to
    every other guard on real capital in this repo."""


def _load_delegate_key(key_path: str):
    """Reads a bare ``solana-keygen``-format JSON array (64 ints — the
    secret key bytes) from ``key_path``. Returns ``(base58_address,
    solders.keypair.Keypair)``. Never logs the private key value — only the
    address, same as everywhere else in the dome (two real secret-display
    incidents, 22-23/07, are why this is stated explicitly rather than
    assumed obvious)."""
    from solders.keypair import Keypair  # type: ignore[import]

    if not key_path:
        raise DelegateKeyError("aucun chemin de clé délégué fourni (fail-closed)")
    try:
        with open(key_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise DelegateKeyError(f"clé délégué illisible ({exc})") from exc

    if not isinstance(data, list) or len(data) != 64:
        raise DelegateKeyError(
            "format de clé délégué inattendu -- attendu un tableau JSON de 64 entiers "
            "(format natif solana-keygen), jamais un objet enveloppé"
        )
    try:
        keypair = Keypair.from_bytes(bytes(data))
    except Exception as exc:  # noqa: BLE001 -- corrupt/invalid key material, fail closed
        raise DelegateKeyError(f"clé délégué invalide ({exc})") from exc

    return str(keypair.pubkey()), keypair


def derive_multisig_pda(multisig_create_key):
    """``multisig_create_key``: ``solders.pubkey.Pubkey``. Returns
    ``(pda, bump)``."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    program_id = Pubkey.from_string(SQUADS_V4_PROGRAM_ID)
    return Pubkey.find_program_address(
        [b"multisig", b"multisig", bytes(multisig_create_key)], program_id,
    )


def derive_spending_limit_pda(multisig_pda, spending_limit_create_key):
    """``multisig_pda``/``spending_limit_create_key``: ``solders.pubkey.Pubkey``.
    Returns ``(pda, bump)``."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    program_id = Pubkey.from_string(SQUADS_V4_PROGRAM_ID)
    return Pubkey.find_program_address(
        [b"multisig", bytes(multisig_pda), b"spending_limit", bytes(spending_limit_create_key)],
        program_id,
    )


def derive_vault_pda(multisig_pda, vault_index: int):
    """``multisig_pda``: ``solders.pubkey.Pubkey``. ``vault_index``: 0-255
    (a single byte on-chain, matching the program's own ``u8``). Returns
    ``(pda, bump)``."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    if not 0 <= vault_index <= 255:
        raise ValueError(f"vault_index hors bornes u8: {vault_index}")
    program_id = Pubkey.from_string(SQUADS_V4_PROGRAM_ID)
    return Pubkey.find_program_address(
        [b"multisig", bytes(multisig_pda), b"vault", bytes([vault_index])], program_id,
    )


def _rpc_call(method: str, params: list, *, client=None, timeout: float = 15.0) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        import httpx  # type: ignore[import]

        if client is not None:
            resp = client.post(_devnet_rpc_url(), json=payload, timeout=timeout)
        else:
            resp = httpx.post(_devnet_rpc_url(), json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 -- network failure, never fabricate a result
        return {"error": str(exc), "result": None}


def read_spending_limit(*, spending_limit_pda: str, client=None) -> dict:
    """Read-only: decodes the REAL ``SpendingLimit`` account right now.
    Returns a dict, never raises on a network/parse failure (mirrors
    ``safe_robinhood_wallet.read_allowance``'s fail-safe pattern) — a caller
    must check ``error`` explicitly rather than assume success."""
    resp = _rpc_call("getAccountInfo", [spending_limit_pda, {"encoding": "base64"}], client=client)
    if resp.get("error"):
        return {"error": str(resp["error"]), "remaining_amount": None}

    value = (resp.get("result") or {}).get("value")
    if value is None:
        return {"error": "compte SpendingLimit introuvable on-chain", "remaining_amount": None}

    try:
        raw = base64.b64decode(value["data"][0])
    except Exception as exc:  # noqa: BLE001 -- malformed RPC payload
        return {"error": f"donnée de compte illisible ({exc})", "remaining_amount": None}

    # Layout: 8 (discriminator) + 32 (multisig) + 32 (create_key) + 1
    # (vault_index) + 32 (mint) + 8 (amount) + 1 (period) + 8
    # (remaining_amount) + 8 (last_reset) + 1 (bump) + ... (members/
    # destinations, unused here) -- verified live this session against the
    # real deployed SpendingLimit (offsets matched the on-chain amount=
    # 3_000_000 / remaining_amount known from the 18/08 milestone).
    if len(raw) < 131:
        return {"error": f"compte SpendingLimit trop court ({len(raw)} octets)", "remaining_amount": None}

    off = 8
    off += 32  # multisig
    off += 32  # create_key
    vault_index = raw[off]
    off += 1
    mint = raw[off:off + 32]
    off += 32
    amount = int.from_bytes(raw[off:off + 8], "little")
    off += 8
    period = raw[off]
    off += 1
    remaining_amount = int.from_bytes(raw[off:off + 8], "little")
    off += 8
    last_reset = int.from_bytes(raw[off:off + 8], "little", signed=True)
    off += 8
    bump = raw[off]

    return {
        "error": None,
        "vault_index": vault_index,
        "mint_is_sol": mint == bytes(32),
        "amount": amount,
        "period": period,
        "remaining_amount": remaining_amount,
        "last_reset": last_reset,
        "bump": bump,
    }


def _encode_spending_limit_use_args(amount: int, decimals: int, memo: str | None) -> bytes:
    """Borsh encoding of ``SpendingLimitUseArgs`` (``instructions/
    spending_limit_use.rs``): ``amount: u64``, ``decimals: u8``,
    ``memo: Option<String>`` (1-byte tag, then a 4-byte LE length + UTF-8
    bytes when present)."""
    body = amount.to_bytes(8, "little") + decimals.to_bytes(1, "little")
    if memo is None:
        body += b"\x00"
    else:
        memo_bytes = memo.encode("utf-8")
        body += b"\x01" + len(memo_bytes).to_bytes(4, "little") + memo_bytes
    return body


def build_spending_limit_use_instruction(
    *, multisig_pda, member, spending_limit_pda, vault_pda, destination,
    amount: int, decimals: int = SOL_DECIMALS, memo: str | None = None,
):
    """Builds the real ``spending_limit_use`` instruction for a native-SOL
    transfer — account order/mut/signer flags taken directly from the real
    fetched on-chain IDL (verified live this session, ``squads_multisig_
    program`` v2.1.0), not guessed. All positional args are
    ``solders.pubkey.Pubkey``.

    The 4 SPL-token-only optional accounts (``mint``/``vaultTokenAccount``/
    ``destinationTokenAccount``/``tokenProgram``) and the fact that this IDL
    predates Anchor's own ``isOptional``-aware anchorpy client are the exact
    thing that crashed anchorpy's generated builder in the one-off script
    this promotes (``docs/HANDOFF_AGENT_WALLET.md``, 18/08) -- worked around
    here the same way: the omitted accounts are filled with the PROGRAM's
    own id as the sentinel Anchor's on-chain runtime recognizes as "this
    Option<Account> is None", their mut/signer flags copied verbatim from
    the IDL regardless of the sentinel (matching what a real anchor-ts
    client would emit)."""
    from solders.instruction import AccountMeta, Instruction  # type: ignore[import]
    from solders.pubkey import Pubkey  # type: ignore[import]
    from solders.system_program import ID as SYSTEM_PROGRAM_ID  # type: ignore[import]

    program_id = Pubkey.from_string(SQUADS_V4_PROGRAM_ID)
    data = _SPENDING_LIMIT_USE_DISCRIMINATOR + _encode_spending_limit_use_args(amount, decimals, memo)

    accounts = [
        AccountMeta(pubkey=multisig_pda, is_signer=False, is_writable=False),
        AccountMeta(pubkey=member, is_signer=True, is_writable=False),
        AccountMeta(pubkey=spending_limit_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=vault_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=destination, is_signer=False, is_writable=True),
        # systemProgram -- present for real (SOL case), never omitted.
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        # mint -- omitted (SPL-token-only), sentinel = program id, isMut=false per IDL.
        AccountMeta(pubkey=program_id, is_signer=False, is_writable=False),
        # vaultTokenAccount -- omitted, sentinel, isMut=true per IDL.
        AccountMeta(pubkey=program_id, is_signer=False, is_writable=True),
        # destinationTokenAccount -- omitted, sentinel, isMut=true per IDL.
        AccountMeta(pubkey=program_id, is_signer=False, is_writable=True),
        # tokenProgram -- omitted, sentinel, isMut=false per IDL.
        AccountMeta(pubkey=program_id, is_signer=False, is_writable=False),
    ]
    return Instruction(program_id=program_id, data=data, accounts=accounts)


def _await_confirmation(signature: str, *, client=None, timeout_s: float = 45.0, poll_interval_s: float = 2.0) -> str:
    """Polls ``getSignatureStatuses`` until the transaction is FINALIZED,
    reported as an on-chain error, or the timeout is reached -- mirrors the
    EVM leg's ``wait_for_transaction_receipt`` doctrine: a returned
    signature alone is never treated as success (a landed transaction can
    still carry a real program error, e.g. the ``6026: Spending limit
    exceeded`` revert already proven live once, 18/08).

    Deliberately requires ``finalized``, NOT ``confirmed`` -- a real race
    found live this session (19/08): a plain ``getAccountInfo`` call (e.g.
    ``read_spending_limit``'s own, RPC default commitment) can still return
    STALE data milliseconds after a transaction reaches only ``confirmed``,
    because ``confirmed`` and ``finalized`` are genuinely different
    commitment levels a few slots apart -- a caller trusting an "ok" status
    at ``confirmed`` and immediately re-reading on-chain state afterward
    could see numbers that have not caught up yet. Costs a little extra
    latency (devnet finalization is normally a handful of seconds), never a
    correctness shortcut on a real-money-adjacent guardrail."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = _rpc_call(
            "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}], client=client,
        )
        if resp.get("error"):
            return f"unknown (statut illisible: {resp['error']})"
        values = (resp.get("result") or {}).get("value") or [None]
        status = values[0]
        if status is not None:
            err = status.get("err")
            if err is not None:
                return f"reverted ({err})"
            if status.get("confirmationStatus") == "finalized":
                return "ok"
        time.sleep(poll_interval_s)
    return "unknown (timeout en attente de confirmation)"


def _sign_and_send_instruction(instruction, payer_keypair, *, client=None, wait_for_confirmation: bool = True) -> dict:
    """Real send path shared by ``send_spending_limit_transfer`` -- fetches
    a fresh blockhash, signs with the delegate key, sends via raw
    ``sendTransaction`` JSON-RPC, and (unless disabled) waits for a real
    confirmed/rejected status. Never raises past this point -- a network/
    send failure is reported as ``{"error": ..., "tx_hash": None}`` so a
    caller (the guardrail wrapper) can log and classify it rather than
    crash."""
    from solders.hash import Hash  # type: ignore[import]
    from solders.transaction import Transaction  # type: ignore[import]

    blockhash_resp = _rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}], client=client)
    if blockhash_resp.get("error"):
        return {"error": f"blockhash indisponible ({blockhash_resp['error']})", "tx_hash": None}
    blockhash_b58 = ((blockhash_resp.get("result") or {}).get("value") or {}).get("blockhash")
    if not blockhash_b58:
        return {"error": "blockhash absent de la réponse RPC", "tx_hash": None}

    try:
        blockhash = Hash.from_string(blockhash_b58)
        tx = Transaction.new_signed_with_payer(
            [instruction], payer_keypair.pubkey(), [payer_keypair], blockhash,
        )
        raw_tx_b64 = base64.b64encode(bytes(tx)).decode("ascii")
    except Exception as exc:  # noqa: BLE001 -- construction/signature failure
        return {"error": f"construction/signature de transaction échouée ({exc})", "tx_hash": None}

    send_resp = _rpc_call(
        "sendTransaction",
        [raw_tx_b64, {"encoding": "base64", "skipPreflight": True, "preflightCommitment": "confirmed"}],
        client=client, timeout=30.0,
    )
    if send_resp.get("error"):
        return {"error": str(send_resp["error"]), "tx_hash": None}
    signature = send_resp.get("result")
    if not signature:
        return {"error": "sendTransaction n'a retourné aucune signature", "tx_hash": None}

    result = {"error": None, "tx_hash": signature, "status": None}
    if wait_for_confirmation:
        result["status"] = _await_confirmation(signature, client=client)
    return result


async def send_spending_limit_transfer(
    *,
    multisig_create_key: str,
    spending_limit_create_key: str,
    vault_index: int,
    destination: str,
    amount: int,
    delegate_key_path: str,
    decimals: int = SOL_DECIMALS,
    memo: str | None = None,
    client=None,
    wait_for_confirmation: bool = True,
) -> dict:
    """Sends a REAL, delegate-signed ``spending_limit_use`` transfer -- the
    production equivalent of the one-off script already proven live (18/08
    HANDOFF entries). Never raises past key-loading; a network/send failure
    is reported as ``{"error": ..., "tx_hash": None}`` so a caller (the
    guardrail wrapper) can log and classify it rather than crash.

    Order, matching the doctrine already established on the EVM leg
    (``safe_robinhood_signer.send_allowance_transfer``): load delegate key
    -> derive the real PDAs -> re-read the REAL remaining spending limit
    on-chain right now (never trust a caller-supplied or cached figure) ->
    reject if ``amount`` exceeds what is actually left -> build the real
    instruction -> sign -> send -> wait for and report the real
    confirmation/error status.

    Declared ``async`` purely to match the injectable ``send_fn`` interface
    used across the dome (``agent_wallet_pilot.py``'s ``swap_fn``/
    ``transfer_fn``, and this file's own EVM sibling) -- the RPC calls
    underneath are synchronous ``httpx``, so this function never actually
    yields control mid-call (same documented interface-consistency rationale
    as ``safe_robinhood_signer.send_allowance_transfer``)."""
    from solders.pubkey import Pubkey  # type: ignore[import]

    address, keypair = _load_delegate_key(delegate_key_path)

    multisig_pda, _ = derive_multisig_pda(Pubkey.from_string(multisig_create_key))
    spending_limit_pda, _ = derive_spending_limit_pda(
        multisig_pda, Pubkey.from_string(spending_limit_create_key),
    )
    vault_pda, _ = derive_vault_pda(multisig_pda, vault_index)

    live = read_spending_limit(spending_limit_pda=str(spending_limit_pda), client=client)
    if live.get("error"):
        return {"error": f"spending limit réelle illisible ({live['error']})", "tx_hash": None}
    remaining = live.get("remaining_amount")
    if remaining is None or amount > remaining:
        return {
            "error": (
                f"montant {amount} > spending limit restante réelle {remaining} "
                "(lue on-chain à l'instant, jamais supposée)"
            ),
            "tx_hash": None,
        }

    instruction = build_spending_limit_use_instruction(
        multisig_pda=multisig_pda,
        member=Pubkey.from_string(address),
        spending_limit_pda=spending_limit_pda,
        vault_pda=vault_pda,
        destination=Pubkey.from_string(destination),
        amount=amount, decimals=decimals, memo=memo,
    )
    return _sign_and_send_instruction(
        instruction, keypair, client=client, wait_for_confirmation=wait_for_confirmation,
    )
