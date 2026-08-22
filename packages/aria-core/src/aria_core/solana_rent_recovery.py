"""Reclaiming the rent deposit locked inside SPL token accounts (22/08).

**The problem this exists for, measured not assumed.** Buying a token on Solana
opens an account to hold it, and opening that account locks 0.00204 SOL of
"rent exemption" -- 0.19 $ at the SOL price the day this was written. That is
NOT a fee: the network gives it back in full when the account is closed. But
nothing in this repo ever closed one, so every token ever bought would have
retired 0.19 $ from a wallet holding 14 $.

The arithmetic is what makes it structural rather than cosmetic. At a 0.10 $
trade size the deposit is nearly TWICE the position, and the late-bonding
pocket surfaces ~40 candidates an hour. The wallet runs out of openable
accounts after 71 distinct tokens, i.e. under two hours -- and it runs out
while still holding almost all of its trading capital. Without this module the
binding constraint on real trading is not the money, it is the deposits.

**Recoverable, unrecoverable, and the difference.** `CloseAccount` demands a
zero token balance, which splits the accounts into four real cases:

  - `empty`     -- already at zero. Closes directly, deposit returned.
  - `dust`      -- holds tokens worth nothing (a rug, a failed exit). Burn
                   first, then close. Both instructions fit in one transaction.
  - `valued`    -- still holds something sellable. NOT touched here: selling is
                   a trading decision belonging to the pocket, never to a
                   cleanup routine that would be silently dumping a position.
  - `frozen`    -- the mint's freeze authority has frozen this account. Cannot
                   transfer, cannot burn, cannot close. The deposit is gone for
                   good, and that is worth surfacing rather than retrying: it
                   is a scam-token signature, and a permanent 0.19 $ loss.

**Deliberately gated and deliberately narrow.** Closing an account sends a real
signed transaction, so it sits behind its own gate, distinct from the trade
pilot's -- cleanup being enabled must never be implied by trading being
enabled. The destination of a reclaimed deposit is ALWAYS the wallet that owns
the account, never a parameter: a "where should the money go" argument on a
routine that moves real value is exactly the shape of a bug that drains a
wallet, so the shape is refused outright.

`inventory()` touches nothing and needs no gate -- it is the half worth running
constantly, since the frozen count alone is a live scam-exposure metric.
"""
from __future__ import annotations

import logging
import os

import httpx

from aria_core.services import solana_gateway
from aria_core.services.solana_rpc_budget import Priority

logger = logging.getLogger(__name__)

# SPL Token program and its instruction indices. Written out rather than pulled
# from a dependency: two one-byte constants do not justify a new package, and
# the values are consensus-frozen -- they cannot drift under us.
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# 22/08, found live: pump.fun mints are Token-2022, NOT the classic program.
# Scanning only the classic one reported an empty wallet while it really held
# three Token-2022 accounts -- the operator spotted the token his own wallet
# app was showing and this module was not. Both must be scanned, and a close
# must be addressed to the SAME program that owns the account: sending a
# Token-2022 account's close to the classic program simply fails.
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_PROGRAM_IDS = (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID)

_IX_BURN = 8
_IX_CLOSE_ACCOUNT = 9

# What the network locks per token account. Read live in `inventory()` -- this
# is only the documented figure, used for projections when no account exists
# yet to measure. Solana has changed rent parameters before.
TYPICAL_RENT_LAMPORTS = 2_039_280

GATE_ENV = "ARIA_SOLANA_RENT_RECOVERY_ENABLED"

# Measured, not guessed: 27 closes is the most that fits in Solana's 1232-byte
# transaction limit (28 compiles to 1260). Batching is not about the fee -- a
# transaction costs 0.00047 $ either way, noise against a 0.10 $ trade. It is
# about keeping cleanup OFF the selling path: a sell that waits on housekeeping
# is a trailing stop firing late, and that costs far more than any fee.
MAX_CLOSES_PER_TRANSACTION = 27

# A token account whose value is below this is treated as dust worth burning to
# free the deposit. Deliberately far under the 0.19 $ deposit: burning is
# irreversible, so the only tokens eligible are ones where holding them cannot
# plausibly beat reclaiming the rent.
DUST_CEILING_USD = 0.01

_REAL_MONEY_LOG_PREFIX = "REAL-MONEY solana-rent-recovery"


class RentRecoveryError(RuntimeError):
    """Raised when a close could not be completed. Never partial, never silent."""


def rent_recovery_enabled() -> bool:
    """Own gate, checked at every attempt. Closed by default."""
    return (os.environ.get(GATE_ENV, "") or "").strip().lower() == "true"


def _classify(parsed: dict, *, value_usd: float | None) -> str:
    """Which of the four real cases an account falls into.

    `value_usd is None` means the price is unknown, and that deliberately maps
    to `valued`, not to `dust`: burning a token because its price feed happened
    to be down would destroy a real position to reclaim 0.19 $.
    """
    if parsed.get("state") == "frozen":
        return "frozen"
    amount = int((parsed.get("tokenAmount") or {}).get("amount") or 0)
    if amount == 0:
        return "empty"
    if value_usd is None:
        return "valued"
    return "dust" if value_usd < DUST_CEILING_USD else "valued"


async def inventory(
    owner_pubkey: str,
    *,
    rpc_http_url: str,
    client: httpx.AsyncClient | None = None,
    price_fn=None,
) -> dict:
    """Read-only census of every token account and the deposit each one holds.

    No gate, no key, nothing sent. Returns per-case totals plus the lamports
    each case could return, so the caller can see what cleanup is worth before
    deciding to run it.

    `price_fn` resolves a mint to a USD value for the held amount; when absent,
    every non-empty account is conservatively `valued` and nothing is burnable.
    """
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    raw: list[tuple[dict, str]] = []
    try:
        for program_id in TOKEN_PROGRAM_IDS:
            # Through the gateway: endpoint choice, rate and failover are not
            # this module's business. NORMAL because an inventory feeds both
            # cleanup and the audit -- late is fine, wrong is not.
            payload = await solana_gateway.call(
                "getTokenAccountsByOwner",
                [owner_pubkey, {"programId": program_id}, {"encoding": "jsonParsed"}],
                priority=Priority.NORMAL,
                client=client,
            )
            if payload is None:
                raise RentRecoveryError(
                    "no endpoint could read token accounts -- refusing to "
                    "report an empty wallet"
                )
            for entry in ((payload or {}).get("result") or {}).get("value") or []:
                # The owning program travels with the account: closing it later
                # must be addressed to the same one.
                raw.append((entry, program_id))
    except Exception as exc:  # noqa: BLE001 -- an unreadable census is not an empty one
        raise RentRecoveryError(f"could not read token accounts: {exc}") from exc
    finally:
        if owns:
            await client.aclose()

    accounts: list[dict] = []
    for entry, program_id in raw:
        try:
            parsed = entry["account"]["data"]["parsed"]["info"]
            lamports = int(entry["account"]["lamports"])
            mint = parsed["mint"]
        except (KeyError, TypeError, ValueError):
            # A shape we do not recognise is skipped, never guessed at: this
            # list decides what gets burned.
            continue

        value_usd = None
        if price_fn is not None:
            try:
                value_usd = await price_fn(mint, parsed.get("tokenAmount") or {})
            except Exception:  # noqa: BLE001 -- unknown price stays unknown
                value_usd = None

        accounts.append(
            {
                "address": entry.get("pubkey"),
                "mint": mint,
                "amount": int((parsed.get("tokenAmount") or {}).get("amount") or 0),
                "decimals": int((parsed.get("tokenAmount") or {}).get("decimals") or 0),
                "rent_lamports": lamports,
                "value_usd": value_usd,
                "program_id": program_id,
                "case": _classify(parsed, value_usd=value_usd),
            }
        )

    totals: dict[str, dict] = {}
    for case in ("empty", "dust", "valued", "frozen"):
        matching = [a for a in accounts if a["case"] == case]
        totals[case] = {
            "count": len(matching),
            "rent_lamports": sum(a["rent_lamports"] for a in matching),
        }

    reclaimable = totals["empty"]["rent_lamports"] + totals["dust"]["rent_lamports"]
    return {
        "accounts": accounts,
        "totals": totals,
        "reclaimable_lamports": reclaimable,
        # Frozen deposits are surfaced as their own figure, never folded into a
        # "locked" total: this money is not waiting, it is lost.
        "lost_to_frozen_lamports": totals["frozen"]["rent_lamports"],
    }


def build_close_instructions(account: dict, owner_pubkey: str) -> list:
    """The instruction(s) that reclaim one account's deposit.

    `dust` needs a burn first; `empty` closes directly. `valued` and `frozen`
    raise rather than returning an empty list -- a caller filtering wrongly
    should hit an error, not a silent no-op that reports success.
    """
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    case = account.get("case")
    if case not in ("empty", "dust"):
        raise RentRecoveryError(f"account {account.get('address')} is '{case}', not closable")

    # The account's own program, never a default: a Token-2022 account closed
    # against the classic program fails outright.
    program = Pubkey.from_string(account.get("program_id") or TOKEN_PROGRAM_ID)
    acct = Pubkey.from_string(account["address"])
    owner = Pubkey.from_string(owner_pubkey)
    instructions = []

    if case == "dust":
        amount = int(account["amount"])
        if amount <= 0:
            raise RentRecoveryError("dust account reported a zero amount -- refusing to burn")
        instructions.append(
            Instruction(
                program_id=program,
                accounts=[
                    AccountMeta(pubkey=acct, is_signer=False, is_writable=True),
                    AccountMeta(
                        pubkey=Pubkey.from_string(account["mint"]),
                        is_signer=False,
                        is_writable=True,
                    ),
                    AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
                ],
                data=bytes([_IX_BURN]) + amount.to_bytes(8, "little"),
            )
        )

    instructions.append(
        Instruction(
            program_id=program,
            accounts=[
                AccountMeta(pubkey=acct, is_signer=False, is_writable=True),
                # Destination is the owner, always. Never a parameter -- see
                # the module docstring.
                AccountMeta(pubkey=owner, is_signer=False, is_writable=True),
                AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
            ],
            data=bytes([_IX_CLOSE_ACCOUNT]),
        )
    )
    return instructions


async def reclaim(
    accounts: list[dict],
    key_path: str,
    *,
    rpc_http_url: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Closes the given accounts and returns their deposits. REAL MONEY.

    Signing and finalization are delegated to `jupiter_swap_signer`'s already
    proven helpers rather than reimplemented -- same key loader (refuses a
    group/world-readable file), same insistence on a real `finalized` status
    before reporting success, since `confirmed` was caught returning
    pre-transaction state on this very chain.

    Refuses rather than partially succeeding: the gate, the kill-switch, and
    the account cases are all checked BEFORE anything is signed. A caller
    passing a `valued` account gets an error from `build_close_instructions`,
    never a quietly skipped entry that would make the returned total wrong.
    """
    if not rent_recovery_enabled():
        raise RentRecoveryError(f"{GATE_ENV} is not enabled -- refusing to close anything")

    # Exactly the checks `solana_trade_pilot` runs, `strict=True` included: a
    # weaker kill-switch on a routine that also signs real transactions would
    # be a bypass, whatever its stated purpose.
    from aria_core import custody_pause, outgoing_pause

    if outgoing_pause.is_paused(strict=True) or custody_pause.is_paused():
        raise RentRecoveryError("kill-switch engaged -- refusing to close anything")

    if not accounts:
        return {"status": "ok", "closed": 0, "reclaimed_lamports": 0, "tx": None}

    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.transaction import VersionedTransaction

    from aria_core.onchain import jupiter_swap_signer as signer

    keypair = signer.load_keypair(key_path)
    owner = str(keypair.pubkey())

    instructions = []
    for account in accounts:
        instructions.extend(build_close_instructions(account, owner))
    expected = sum(int(a["rent_lamports"]) for a in accounts)

    owns = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        data = await signer._rpc(
            "getLatestBlockhash", [{"commitment": "finalized"}],
            rpc_http_url=rpc_http_url, client=client,
        )
        blockhash = ((data.get("result") or {}).get("value") or {}).get("blockhash")
        if not blockhash:
            raise RentRecoveryError("no blockhash returned -- refusing to build a transaction")

        message = MessageV0.try_compile(
            keypair.pubkey(), instructions, [], Hash.from_string(blockhash)
        )
        tx = VersionedTransaction(message, [keypair])
        import base64

        encoded = base64.b64encode(bytes(tx)).decode()

        # Simulated first, always. A close that would fail on-chain must not
        # consume a fee and must not be reported as a reclaim.
        sim = await signer._rpc(
            "simulateTransaction", [encoded, {"encoding": "base64", "commitment": "processed"}],
            rpc_http_url=rpc_http_url, client=client,
        )
        err = ((sim.get("result") or {}).get("value") or {}).get("err")
        if err:
            raise RentRecoveryError(f"simulation rejected the close: {err}")

        sent = await signer._rpc(
            "sendTransaction",
            [encoded, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
            rpc_http_url=rpc_http_url, client=client,
        )
        signature = sent.get("result")
        if not signature:
            raise RentRecoveryError("sendTransaction returned no signature")

        logger.warning(
            "%s: closing %d account(s), reclaiming %d lamports, tx %s",
            _REAL_MONEY_LOG_PREFIX, len(accounts), expected, signature,
        )
        status = await signer._await_finalized(
            signature, rpc_http_url=rpc_http_url, client=client
        )
        return {
            "status": status,
            "closed": len(accounts) if status == "ok" else 0,
            # Only claimed on a real finalized status -- an `unknown` outcome
            # reports zero reclaimed rather than an optimistic figure.
            "reclaimed_lamports": expected if status == "ok" else 0,
            "tx": signature,
        }
    finally:
        if owns:
            await client.aclose()


async def sweep(
    owner_pubkey: str,
    key_path: str,
    *,
    rpc_http_url: str,
    price_fn=None,
    max_batches: int = 4,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Census, then close everything reclaimable, in batches. REAL MONEY.

    This is the shape the pocket should call -- deliberately NOT a hook on the
    sell path. Closing an account is a second transaction, and putting it in
    the critical path of an exit means a trailing stop waits on housekeeping.
    A periodic sweep costs the same fees and delays nothing.

    `max_batches` bounds one run rather than looping until dry: an unbounded
    cleanup that meets a persistent RPC failure would retry forever. Leftovers
    are reported and picked up by the next sweep.

    A failed batch stops the run instead of pressing on -- if closes are being
    rejected, the next batch will be too, and each attempt costs a real fee.
    """
    census = await inventory(
        owner_pubkey, rpc_http_url=rpc_http_url, price_fn=price_fn, client=client
    )
    closable = [a for a in census["accounts"] if a["case"] in ("empty", "dust")]

    result = {
        "candidates": len(closable),
        "closed": 0,
        "reclaimed_lamports": 0,
        "txs": [],
        "remaining": len(closable),
        "lost_to_frozen_lamports": census["lost_to_frozen_lamports"],
    }
    if not closable:
        return result

    for index in range(max_batches):
        start = index * MAX_CLOSES_PER_TRANSACTION
        batch = closable[start : start + MAX_CLOSES_PER_TRANSACTION]
        if not batch:
            break
        outcome = await reclaim(
            batch, key_path, rpc_http_url=rpc_http_url, client=client
        )
        result["txs"].append(outcome["tx"])
        result["closed"] += outcome["closed"]
        result["reclaimed_lamports"] += outcome["reclaimed_lamports"]
        if outcome["status"] != "ok":
            logger.warning(
                "%s: batch %d ended '%s' -- stopping this sweep, %d account(s) left",
                _REAL_MONEY_LOG_PREFIX, index, outcome["status"],
                len(closable) - result["closed"],
            )
            break

    result["remaining"] = len(closable) - result["closed"]
    return result


def projected_capacity(spendable_lamports: int, *, rent_lamports: int = TYPICAL_RENT_LAMPORTS) -> dict:
    """How many DISTINCT tokens a balance can hold open at once.

    The figure that makes the constraint legible: at 0.10 $ a trade the money
    is never what runs out first.
    """
    if rent_lamports <= 0:
        raise RentRecoveryError("rent_lamports must be positive")
    slots = max(0, spendable_lamports) // rent_lamports
    return {
        "distinct_tokens": int(slots),
        "rent_lamports_each": rent_lamports,
        "total_locked_at_capacity": int(slots * rent_lamports),
    }
