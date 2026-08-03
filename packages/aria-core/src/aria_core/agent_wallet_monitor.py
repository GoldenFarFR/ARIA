"""READ-ONLY monitoring of the CDP agent wallet -- complete ledger of real
movements (deposits/withdrawals), auto-detected via Blockscout (read only,
no private key, no execution capability). Answers the operator request from
07/16: "automatic detection of funds coming in or out with a complete wallet
ledger so you can verify in real time".

Reuses `services/blockscout.py` (already built, Base-native, #157) rather than
duplicating a client -- `get_token_transfers` for USDC (ERC-20),
`get_transactions` for native ETH (gas/deposits). No new network client.

Each freshly detected movement (never reviewed twice, unique `tx_hash`) is
classified as:
  - "known": the `tx_hash` matches a transaction already logged by
    `agent_wallet_pilot`/`agent_wallet_log` (ARIA herself initiated this
    movement) -- nothing abnormal.
  - "external_deposit": incoming funds not initiated by ARIA (e.g. the
    operator funds the wallet manually) -- normal, logged for traceability.
  - "internal_transfer": movement (either direction) whose counterparty is
    ANOTHER wallet ARIA/the operator already controls (cf.
    `_KNOWN_ADDRESS_NAMES` -- the Smart Accounts, the delegated spender, the
    transfer wallet, the two Tangem owners) -- 23/07, operator request after
    a real false alarm: money staying inside the ARIA/operator ecosystem is
    never a leak, even when the specific tx_hash wasn't logged by
    `agent_wallet_log` (e.g. a manual Tangem-signed transfer, or a future
    Smart Account swing round-trip not yet wired into that log).
  - "unexpected_outflow": OUTGOING funds not initiated by ARIA AND not going
    to a wallet ARIA/the operator already controls -- a potentially serious
    security signal (compromised key?), to be handled urgently by the caller
    (immediate notification).

Honest limitation assumed: the "known" classification can ONLY match
movements that went through `agent_wallet_pilot.py` (logged swap/transfer) --
a movement initiated by another legitimate tool (e.g. the operator using the
Coinbase app directly) will be classified "external_deposit"/
"unexpected_outflow" even though it's actually the operator acting. Not a
flaw: a false positive (operator re-confirms it was indeed them) beats a
silent false negative.

Structurally separate from `wallet_guard.py` and `agent_wallet_pilot.py` (no
cross-import for execution) -- this module CANNOT sign or execute anything,
only read and log."""
from __future__ import annotations

import html
import json
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS
from aria_core.paths import aria_db_path
from aria_core.services.blockscout import get_blockscout_client
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# PUBLIC Base mainnet address of the CDP agent wallet (`aria-agent-wallet-pilot`,
# verified live on 07/16 -- cf. agent_wallet_cdp_adapter.py). A public
# address isn't a secret (unlike CDP keys) -- hardcoded here
# the same way as ALLOWED_TRANSFER_ADDRESS in agent_wallet_pilot.py.
# Kept as-is (compat): `get_wallet_balance_summary`/`/agentwallet` continue to
# default to it, signature unchanged.
MONITORED_WALLET_ADDRESS = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"

# 07/23 -- surveillance extended beyond the single historical pilot wallet
# (operator request: "automatically detect entries, exits, and swaps on her
# wallets in real time"). Addresses verified live on 07/23 via
# `cdp.evm.list_accounts()`/`list_smart_accounts()` (CDP source of truth,
# never copied from memory).
#
# Scope originally limited to 3 wallets (explicit operator decision, 07/23,
# after computing the real cost in Blockscout Pro credits) -- excluded at the
# time: `aria-wallet-transfert-EVM` (rarely used, still excluded) and
# `aria-spender-smart-st-EVM` ("zero movement" at the time, the Spend
# Permission mechanism wasn't wired up yet).
#
# Item #192 (29/07): `aria-spender-smart-st-EVM`'s premise no longer holds --
# verified live via Blockscout that it received a real movement since (a
# phishing/dust airdrop, an ERC-20 token named "Claude" sent unsolicited on
# 07/25, plus a legitimate small gas top-up from `aria-wallet-X402-EVM`).
# Re-added to real-time surveillance now that it's a genuinely active
# address, per the 07/23 comment's own stated criterion.
# `aria-wallet-transfert-EVM` stays excluded (still no movement observed).
#
# `aria-wallet-X402-EVM` stays monitored -- its role will change (active
# trading capital migrates to `aria-smart-st-EVM`, this wallet will
# eventually only hold x402 payments in and out for the services offered)
# but remains a real flow worth watching, not less than before.
#
# Item #192 continued (29/07): 2 more real CDP Smart Accounts exist
# (`cdp.evm.list_smart_accounts()`, verified live) beyond the 2 already
# monitored above, named `0-OLD-smart-wallet-one`/`0-OLD-smart-wallet-two`
# in CDP -- zero balance, zero transaction on Blockscout at the time of
# this check (genuinely dormant, not yet renamed/repurposed). Operator
# intent (29/07): one becomes the future x402 Smart Account (Item #191,
# migrating aria-wallet-X402-EVM off its current EOA), the other becomes a
# dedicated scalping Smart Account (Item #103, "smart-sc", separate from
# aria-smart-st-EVM) -- which specific address gets which role isn't
# decided yet, so both are added to surveillance NOW, ahead of that
# decision, rather than waiting for the rename and risking a gap the day
# either goes live. Kept under their current CDP names until renamed.
#
# Deliberate dict order: pilot wallet first (existing tests that use
# MONITORED_WALLET_ADDRESS as the recipient depend on this order to be
# classified correctly before `_already_seen` cuts short the following
# wallets in the same transaction).
MONITORED_WALLETS: dict[str, str] = {
    "aria-wallet-X402-EVM": MONITORED_WALLET_ADDRESS,
    "aria-smart-st-EVM": "0x800027f61363EF304c5C2Afee811d9d4074B474c",
    "aria-smart-vc-EVM": "0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07",
    "aria-spender-smart-st-EVM": "0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b",
    "0-OLD-smart-wallet-one": "0x81e26A7552e15D3B4cE50b505A382773b1CA0089",
    "0-OLD-smart-wallet-two": "0xDa1a87f38E78Eb9564D135804414E089519C2B1c",
}

# 07/23 -- registry of ALL known addresses (not just the 3 monitored ones --
# transfert/spender remain legitimate possible counterparties of a movement
# on a monitored wallet) + the 2 physical Tangem owners, to show a readable
# name in alerts instead of a raw hex address (operator request, 07/23: "for
# wallets we know, show the name"). Addresses verified live (CDP + explicitly
# communicated by the operator), never copied from memory.
_KNOWN_ADDRESS_NAMES: dict[str, str] = {
    "0xF04625162b616c5ad9788811b7be8CDd425B37Ef".lower(): "aria-wallet-X402-EVM",
    "0x584b2B35dac347B2317da0d21b95063de51257Ef".lower(): "aria-wallet-transfert-EVM",
    "0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b".lower(): "aria-spender-smart-st-EVM",
    "0x800027f61363EF304c5C2Afee811d9d4074B474c".lower(): "aria-smart-st-EVM",
    "0x9C72AedD2836Edc24566E8B0Fd1825e0E1eFbF07".lower(): "aria-smart-vc-EVM",
    "0x81e26A7552e15D3B4cE50b505A382773b1CA0089".lower(): "0-OLD-smart-wallet-one",
    "0xDa1a87f38E78Eb9564D135804414E089519C2B1c".lower(): "0-OLD-smart-wallet-two",
    "0x33783cCb570Cb279C25F836806B5c4C3C8309777".lower(): "tangem-01 (owner aria-smart-st)",
    "0x85e3D8128a9b7be14065A4E36C1845041BF65d7F".lower(): "tangem-02 (owner aria-smart-vc)",
}

# 29/07 -- registry of THIRD-PARTY addresses manually identified by the
# operator AFTER an alert (e.g. a legitimate payment to an external service),
# for DISPLAY ONLY -- deliberately NEVER consulted by `_is_own_wallet` (never
# imported/merged into `_KNOWN_ADDRESS_NAMES`). A future movement to/from one
# of these addresses stays classified exactly as it would without this
# registry (e.g. still "unexpected_outflow" if unmatched by known_x402) --
# labeling an address here is a readability aid, never a trust decision. A
# real x402 payment ALREADY gets recognized automatically via
# `_matches_known_x402` (pay_to/amount/time-window) -- this registry exists
# for the case that mechanism can't cover: a one-off manual payment (e.g. the
# operator paying a SaaS invoice) that will never appear in `x402_budget`.
_THIRD_PARTY_ADDRESS_NAMES: dict[str, str] = {
    "0x0929222bC7Cc533aecC1ccFe9d9Bd6ecbB0CBF43".lower(): "Codex.io (paiement verification compte, confirme operateur 29/07)",
    "0x79aD858cDff50ca6728D2531894001F221e805c7".lower(): "Codex.io - frais de reglement (meme paiement, 29/07)",
    # OpenRouter -- each recharge appears to use a distinct deposit address
    # sharing the same "...0Ff6" suffix (payment-processor pattern, not a
    # fixed address) -- confirmed operateur 08/02 (20 USDC) and 08/01 (20
    # USDC), both approved via the interactive outflow confirmation. A
    # future recharge will very likely use YET ANOTHER prefix and won't
    # match this entry -- labeling is a readability aid for these two
    # already-resolved alerts, never a guarantee against a future one.
    "0xdfd0aA6F750B92618E65bed84b8077a520660Ff6".lower(): "OpenRouter (recharge USDC, confirme operateur 08/02)",
    "0xdfd056b89df165453D6c30c1Cb80d3167EaA0Ff6".lower(): "OpenRouter (recharge USDC, confirme operateur 08/01)",
}


def _is_own_wallet(address: str) -> bool:
    """``True`` if ``address`` is one of the wallets ARIA/the operator
    already controls (reuses ``_KNOWN_ADDRESS_NAMES`` -- never a second list,
    same "sobriété" doctrine as the rest of the project: don't duplicate a
    registry that already exists). A movement to/from any of these addresses
    is, by construction, never a leak outside the ARIA/operator ecosystem --
    23/07, operator request after a real false alarm (a legitimate x402
    payment misclassified as an unexplained outflow)."""
    return bool(address) and address.lower() in _KNOWN_ADDRESS_NAMES


def _label_address(address: str) -> str:
    """Address enriched with the known name in parentheses
    (``"tangem-01 (0x3378...)"``) if it matches an already-registered
    ARIA wallet/owner, OR a manually-identified third party (cf.
    ``_THIRD_PARTY_ADDRESS_NAMES`` -- display only, never affects
    classification) -- otherwise the raw address unchanged. Never blocking,
    never a guess on an unknown address."""
    if not address:
        return address
    name = _KNOWN_ADDRESS_NAMES.get(address.lower()) or _THIRD_PARTY_ADDRESS_NAMES.get(address.lower())
    return f"{name} ({address})" if name else address


def agent_wallet_monitor_enabled() -> bool:
    """Dedicated gate, independent from the pilot/swap/transfer gates --
    monitoring can run even while execution stays disabled (read-only, no
    risk in leaving it active more broadly than execution itself)."""
    return os.environ.get("ARIA_AGENT_WALLET_MONITOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class WalletMovement:
    tx_hash: str
    direction: str  # "in" | "out" | "swap"
    asset: str
    amount: float
    counterparty: str
    classification: str  # "known" | "external_deposit" | "internal_transfer" | "unexpected_outflow" | "suspicious_token" | "known_x402" | "swap"
    timestamp: str | None = None
    # 07/22 -- enrichment of the "known_x402" alert (which token was scanned,
    # which service was paid, cf. x402_budget.record_spend). Optional and empty
    # by default: backward compatible, no other movement (known/external_deposit/
    # unexpected_outflow/suspicious_token) ever populates them.
    contract: str = ""
    token_symbol: str = ""
    resource: str = ""
    provider: str = ""
    # 07/23 -- multi-wallet: which wallet moved (empty -> "Wallet agent"
    # default in the alert, unchanged historical behavior for movements
    # built without this field, e.g. all existing tests).
    wallet_name: str = ""
    # 07/23 -- swap: two legs of the same transaction (one asset out, another
    # in) merged into a SINGLE movement rather than two rows fighting over
    # the same tx_hash (UNIQUE DB constraint). When classification == "swap",
    # `asset`/`amount` carry the RECEIVED side, `asset_out`/`amount_out` the
    # GIVEN side. Empty for any other movement.
    asset_out: str = ""
    amount_out: float = 0.0
    # 29/07 -- Item #187, post-incident (operator stress-test on aria-wallet-
    # X402-EVM): the on-chain method name (e.g. "swapAndExecute",
    # "transferWithAuthorization"), already available from Blockscout but
    # never surfaced in the alert before -- an investigator previously had to
    # dig into Blockscout by hand to see it. Empty for native ETH movements
    # (no method call) and for any test double that doesn't populate it.
    method: str = ""


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_wallet_movement_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_hash TEXT NOT NULL UNIQUE,
                direction TEXT NOT NULL,
                asset TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                counterparty TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                tx_timestamp TEXT
            )
            """
        )
        # 07/23 -- hot migration: multi-wallet + swap columns (SQLite doesn't
        # create them if the table already pre-exists). Idempotent,
        # non-destructive -- same pattern as paper_trader.py (PRAGMA
        # table_info then ALTER TABLE only if the column is missing).
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(agent_wallet_movement_log)")).fetchall()
        }
        for name, ddl in (
            ("wallet_name", "TEXT NOT NULL DEFAULT ''"),
            ("asset_out", "TEXT NOT NULL DEFAULT ''"),
            ("amount_out", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in existing:
                await db.execute(f"ALTER TABLE agent_wallet_movement_log ADD COLUMN {name} {ddl}")
        # 07/23 -- persisted registry of wallets/contracts already detected
        # as a phishing attempt (address poisoning / fake token impersonating
        # a tracked asset) -- operator request: keep track of who has already
        # tried this kind of attack against our wallets, never just a finding
        # lost cycle after cycle. `first_seen_tx_hash` = the very first
        # attempt that revealed this address -- useful to trace context
        # without re-searching the movement log.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS phishing_blacklist (
                address TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                impersonated_asset TEXT NOT NULL DEFAULT '',
                first_seen_tx_hash TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        await db.commit()


async def _already_seen(tx_hash: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM agent_wallet_movement_log WHERE tx_hash = ?", (tx_hash,)
            )
        ).fetchone()
    return row is not None


async def _record_movement(m: WalletMovement) -> bool:
    """Writes the movement -- ``True`` only if THIS attempt actually created
    the row (never notify on a classification that lost the race).

    07/20 -- real bug found under real conditions (operator screenshot, an
    "UNINITIATED OUTFLOW" alert on an x402 payment that was actually already
    known): the old ``_already_seen()`` (check) then ``_record_movement()``
    (act) wasn't atomic -- two passes that both see ``_already_seen() ->
    False`` before either writes could each compute THEIR OWN classification
    (potentially different if their read of ``known_x402_spends`` wasn't at
    the same instant) and BOTH notify, even though only one of the two rows
    actually wins the write (``INSERT OR IGNORE``, unique ``tx_hash``). The
    loser still notified with its potentially stale classification. Fixed:
    the actual write outcome (``rowcount``) now decides whether THIS attempt
    is allowed to notify -- never the classification computed in memory alone."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO agent_wallet_movement_log
                (tx_hash, direction, asset, amount, counterparty, classification,
                 detected_at, tx_timestamp, wallet_name, asset_out, amount_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m.tx_hash, m.direction, m.asset, m.amount, m.counterparty,
                m.classification, datetime.now(timezone.utc).isoformat(), m.timestamp,
                m.wallet_name, m.asset_out, m.amount_out,
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def record_phishing_attempt(
    address: str, *, kind: str, impersonated_asset: str = "", tx_hash: str = "",
) -> None:
    """Records (or increments ``occurrences`` if already known) an address
    involved in a detected phishing attempt (``kind`` = ``"wallet"`` for the
    sender of a fake token, ``"contract"`` for the fake token's contract
    itself). Never blocking: a write failure here must never lose the
    `suspicious_token` movement already logged elsewhere (called after
    `_record_movement`, never before)."""
    if not address:
        return
    await _ensure_table()
    address_lower = address.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (
            await db.execute("SELECT occurrences FROM phishing_blacklist WHERE address = ?", (address_lower,))
        ).fetchone()
        if existing is None:
            await db.execute(
                """
                INSERT INTO phishing_blacklist
                    (address, kind, impersonated_asset, first_seen_tx_hash, first_seen_at, occurrences)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (address_lower, kind, impersonated_asset, tx_hash, datetime.now(timezone.utc).isoformat()),
            )
        else:
            await db.execute(
                "UPDATE phishing_blacklist SET occurrences = occurrences + 1 WHERE address = ?",
                (address_lower,),
            )
        await db.commit()


async def is_known_phishing_address(address: str) -> bool:
    """Checks whether ``address`` is already known as a phishing wallet or
    contract -- useful for a future guard before an outgoing transfer (the
    current pilot doesn't need it, locked to a single fixed address via
    ``ALLOWED_TRANSFER_ADDRESS``, but useful once a less-constrained
    mechanism exists). Cautious fail-closed: an empty address is never
    "known"."""
    if not address:
        return False
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM phishing_blacklist WHERE address = ?", (address.lower(),)
            )
        ).fetchone()
    return row is not None


async def list_phishing_addresses(limit: int = 100) -> list[dict]:
    """Full registry of detected phishing attempts, most recent (by first
    appearance) first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM phishing_blacklist ORDER BY first_seen_at DESC LIMIT ?", (limit,)
            )
        ).fetchall()
    return [dict(r) for r in rows]


# 07/17 -- tolerance window to correlate an on-chain movement detected with an
# already-logged x402 payment (signature -> settlement -> Blockscout indexing
# introduces a real delay, never instant) -- generous but bounded, never wide
# enough to match two unrelated payments.
_X402_MATCH_WINDOW_MINUTES = 30
_X402_MATCH_AMOUNT_EPSILON = 0.001  # USDC -- tolerates float rounding, no more


def _parse_timestamp(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _matches_known_x402(
    *, counterparty: str, amount: float, timestamp: str | None, known_x402_spends: list[dict],
) -> dict | None:
    """An outgoing movement matches an already-logged x402 payment (same
    recipient, same amount within rounding, within the time window) --
    ALWAYS fails toward "no match" in case of doubt (missing data, unreadable
    timestamp): a false positive alert beats a silent false negative, same
    doctrine as the rest of this module.

    07/22 -- now returns the dict of the matched spend (contract/token_symbol/
    resource/provider, cf. `x402_budget.record_spend`) instead of a plain bool,
    to let the alert display WHICH token/service was paid for -- matching
    logic strictly unchanged, only the return value changes."""
    counterparty_lower = (counterparty or "").lower()
    if not counterparty_lower:
        return None
    movement_ts = _parse_timestamp(timestamp)
    if movement_ts is None:
        return None
    for spend in known_x402_spends:
        if (spend.get("pay_to") or "").lower() != counterparty_lower:
            continue
        if abs(float(spend.get("amount_usd") or 0.0) - amount) > _X402_MATCH_AMOUNT_EPSILON:
            continue
        spend_ts = _parse_timestamp(spend.get("created_at"))
        if spend_ts is None:
            continue
        if abs((movement_ts - spend_ts).total_seconds()) <= _X402_MATCH_WINDOW_MINUTES * 60:
            return spend
    return None


def _classify(
    tx_hash: str, direction: str, known_tx_hashes: set[str],
    *, counterparty: str = "", amount: float = 0.0, timestamp: str | None = None,
    known_x402_spends: list[dict] | None = None,
) -> tuple[str, dict | None]:
    """Returns ``(classification, matched_spend)`` -- ``matched_spend`` is only
    ever non-``None`` when the classification is ``"known_x402"`` (07/22, to
    enrich the alert with the paid token/service), ``None`` in every other case."""
    if tx_hash in known_tx_hashes:
        return "known", None
    if direction == "in":
        if _is_own_wallet(counterparty):
            return "internal_transfer", None
        return "external_deposit", None
    if known_x402_spends:
        matched = _matches_known_x402(
            counterparty=counterparty, amount=amount, timestamp=timestamp,
            known_x402_spends=known_x402_spends,
        )
        if matched is not None:
            return "known_x402", matched
    if _is_own_wallet(counterparty):
        return "internal_transfer", None
    return "unexpected_outflow", None


def _x402_movement_fields(spend: dict | None) -> dict[str, str]:
    """Extracts contract/token_symbol/resource/provider from a matched x402 spend
    to populate a `WalletMovement` -- empty dict (so "" everywhere) if `spend`
    is `None`, never a half-filled field."""
    spend = spend or {}
    return {
        "contract": spend.get("contract") or "",
        "token_symbol": spend.get("token_symbol") or "",
        "resource": spend.get("resource") or "",
        "provider": spend.get("provider") or "",
    }


# Official addresses of the ONLY assets the operator tracks by NAME in alerts
# (native ETH, no contract -- any ERC-20 "ETH" is an impersonator by
# construction; USDC reuses the same address as real execution -- never two
# sources of truth). Found under real conditions (07/17): two "dust" deposits
# received the same day impersonated ETH/USDC via a Unicode homoglyph (e.g.
# "EṬH", T with combining dot below, visually indistinguishable from "ETH" on
# a small Telegram screen) from 0-holder ERC-20 contracts -- the alert
# displayed the symbol as-is, without checking it against the token's real
# address, a real risk for an operator who has to decide quickly whether a
# deposit is legitimate.
_CANONICAL_ASSET_ADDRESSES: dict[str, str | None] = {
    "ETH": None,  # natif seulement -- aucun contrat ERC-20 légitime ne peut porter ce nom
    "USDC": USDC_BASE_ADDRESS,
}


def _normalize_symbol(symbol: str) -> str:
    """Decomposes Unicode diacritics (NFKD) and strips them -- ``"EṬH"`` (T +
    U+0323 combining dot below) becomes ``"ETH"`` again, exactly the attack a
    plain ``.upper()`` does NOT detect (combining characters aren't letters,
    ``.upper()`` leaves them unchanged)."""
    decomposed = unicodedata.normalize("NFKD", symbol or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.strip().upper()


def _lookalike_target(symbol: str | None, token_address: str | None) -> str | None:
    """Returns the name of the REAL asset being impersonated (``"ETH"``/``"USDC"``)
    if the transfer's symbol resembles one of the tracked assets once
    diacritics are stripped, but the contract does NOT match the official
    address -- ``None`` if the symbol doesn't resemble anything tracked, or if
    it's authentically the right contract."""
    normalized = _normalize_symbol(symbol or "")
    if normalized not in _CANONICAL_ASSET_ADDRESSES:
        return None
    real_address = _CANONICAL_ASSET_ADDRESSES[normalized]
    if real_address is None:
        return normalized  # impersonates native ETH -- an ERC-20 transfer can never legitimately be this
    if (token_address or "").lower() != real_address.lower():
        return normalized
    return None


async def list_recent_movements(limit: int = 100) -> list[dict]:
    """Full persisted registry (append-only in practice -- `INSERT OR IGNORE`
    never rewrites an existing row), most recent first."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM agent_wallet_movement_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


def _try_build_swap(tx_hash: str, events: list[dict], *, wallet_name: str) -> WalletMovement | None:
    """Merges two opposite movements (one out + one in) sharing the same
    ``tx_hash`` into a SINGLE ``WalletMovement`` classified ``"swap"`` --
    without this merge, the two legs would fight over the same DB row
    (``tx_hash`` UNIQUE) and the second would be silently lost, never
    notified (latent bug in the pre-07/23 code, fixed in passing by this
    restructuring). Returns ``None`` (handle each event individually,
    historical behavior) if: not exactly 2 events, same direction on both
    sides, same asset on both sides (not a real exchange), or one of the two
    legs impersonates a tracked asset -- never hide a security alert behind
    a swap that looks legitimate."""
    if len(events) != 2:
        return None
    a, b = events
    if a["direction"] == b["direction"]:
        return None
    for ev in (a, b):
        if ev["kind"] == "token" and _lookalike_target(ev.get("token_symbol"), ev.get("token_address")):
            return None
    if a["asset"] == b["asset"]:
        return None
    incoming, outgoing = (a, b) if a["direction"] == "in" else (b, a)
    return WalletMovement(
        tx_hash=tx_hash, direction="swap",
        asset=incoming["asset"], amount=incoming["amount"],
        asset_out=outgoing["asset"], amount_out=outgoing["amount"],
        counterparty=outgoing["counterparty"] or incoming["counterparty"],
        classification="swap",
        timestamp=incoming["timestamp"] or outgoing["timestamp"],
        wallet_name=wallet_name,
    )


async def check_wallet_activity(
    *, wallet_address: str, chain: str = "base", wallet_name: str = "",
    known_tx_hashes: set[str] | None = None,
    known_x402_spends: list[dict] | None = None,
) -> list[WalletMovement]:
    """Queries Blockscout (read-only) for recent USDC (ERC-20) transfers AND
    native ETH movements of ``wallet_address``, logs every NEW movement
    (never reviewed twice thanks to the uniqueness of ``tx_hash``) and
    returns the list of freshly detected movements (for notification by the
    caller -- this module never notifies by itself, see
    ``format_movement_alert``).

    ``wallet_name`` (07/23): name of the monitored wallet (cf.
    ``MONITORED_WALLETS``) -- propagated onto every ``WalletMovement``
    produced, shown at the top of the alert. Empty by default (backward
    compatible, shows "Wallet agent").

    ``known_tx_hashes``: hashes already logged by ``agent_wallet_log``
    (transactions initiated by ARIA herself, ok status only) -- typically
    ``{row['tx_hash'] for row in await agent_wallet_log.list_transactions() if row['status'] == 'ok'}``.

    ``known_x402_spends`` (07/17): settled x402 spends (``status='ok'``) --
    typically ``await x402_budget.list_spends()`` filtered -- correlated by
    recipient+amount+time window (not by ``tx_hash``, never guaranteed
    available on the payer side in the x402 protocol, cf. ``_matches_known_x402``).

    07/23 -- swap detection: both sources (token transfers + native tx) are
    first collected WITHOUT being recorded, grouped by ``tx_hash`` -- a group
    with one entry and one exit on two different assets becomes a single
    ``"swap"`` movement (cf. ``_try_build_swap``) rather than two classic
    movements."""
    await _ensure_table()
    known_tx_hashes = known_tx_hashes or set()
    known_x402_spends = known_x402_spends or []
    address_lower = wallet_address.lower()
    client = get_blockscout_client(chain)
    fresh: list[WalletMovement] = []

    raw_by_tx: dict[str, list[dict]] = {}

    token_result = await client.get_token_transfers(wallet_address, limit=50, max_pages=1)
    if not token_result.available:
        logger.info("agent_wallet_monitor: token transfers unavailable (%s)", token_result.error)
    else:
        for t in token_result.transfers:
            if not t.tx_hash or await _already_seen(t.tx_hash):
                continue
            direction = "in" if (t.to_address or "").lower() == address_lower else "out"
            counterparty = (t.from_address if direction == "in" else t.to_address) or ""
            raw_by_tx.setdefault(t.tx_hash, []).append({
                "kind": "token", "direction": direction, "asset": t.token_symbol or "token",
                "amount": t.amount or 0.0, "counterparty": counterparty, "timestamp": t.timestamp,
                "token_address": t.token_address, "token_symbol": t.token_symbol,
                "method": t.method or "",
            })

    tx_result = await client.get_transactions(wallet_address, limit=50)
    if not tx_result.available:
        logger.info("agent_wallet_monitor: native transactions unavailable (%s)", tx_result.error)
    else:
        for tx in tx_result.transactions:
            if not tx.tx_hash or await _already_seen(tx.tx_hash):
                continue
            if not tx.value_native or tx.value_native <= 0:
                continue  # contract call with no value transferred -- not a funds movement
            direction = "in" if (tx.to_address or "").lower() == address_lower else "out"
            counterparty = (tx.from_address if direction == "in" else tx.to_address) or ""
            raw_by_tx.setdefault(tx.tx_hash, []).append({
                "kind": "native", "direction": direction, "asset": "ETH",
                "amount": tx.value_native, "counterparty": counterparty, "timestamp": tx.timestamp,
                "token_address": None, "token_symbol": None, "method": "",
            })

    for tx_hash, events in raw_by_tx.items():
        swap_movement = _try_build_swap(tx_hash, events, wallet_name=wallet_name)
        if swap_movement is not None:
            if await _record_movement(swap_movement):
                fresh.append(swap_movement)
            continue

        for ev in events:
            lookalike = None
            if ev["kind"] == "token":
                classification, matched_spend = _classify(
                    tx_hash, ev["direction"], known_tx_hashes,
                    counterparty=ev["counterparty"], amount=ev["amount"], timestamp=ev["timestamp"],
                    known_x402_spends=known_x402_spends,
                )
                asset_label = ev["asset"]
                lookalike = _lookalike_target(ev.get("token_symbol"), ev.get("token_address"))
                if lookalike:
                    asset_label = f"{asset_label} (FAUX {lookalike} -- contrat non officiel)"
                    classification = "suspicious_token"
                    matched_spend = None  # a spoofed token never inherits x402 info
            else:
                # 07/22 -- no known_x402_spends here: an x402 payment settles in
                # USDC (cf. x402_budget), never in native ETH -- x402 classification/
                # enrichment structurally doesn't apply to this branch, unchanged
                # behavior (matched_spend always None).
                # 07/23 -- `counterparty` now passed through: a native ETH transfer
                # (e.g. a gas top-up) between two of ARIA's own wallets must be
                # recognized as "internal_transfer" exactly like a token transfer.
                classification, matched_spend = _classify(
                    tx_hash, ev["direction"], known_tx_hashes, counterparty=ev["counterparty"],
                )
                asset_label = ev["asset"]

            movement = WalletMovement(
                tx_hash=tx_hash, direction=ev["direction"], asset=asset_label,
                amount=ev["amount"], counterparty=ev["counterparty"],
                classification=classification, timestamp=ev["timestamp"],
                wallet_name=wallet_name, method=ev.get("method", ""),
                **_x402_movement_fields(matched_spend),
            )
            if await _record_movement(movement):
                fresh.append(movement)
                if classification == "suspicious_token":
                    # 07/23 -- anti-phishing registry (operator request): remembers
                    # both the fake token's CONTRACT and the WALLET that sent it.
                    # Best-effort, never blocking -- a write failure here must
                    # never lose the movement already logged above.
                    try:
                        await record_phishing_attempt(
                            ev.get("token_address") or "", kind="contract",
                            impersonated_asset=lookalike or "", tx_hash=tx_hash,
                        )
                        await record_phishing_attempt(
                            ev["counterparty"], kind="wallet",
                            impersonated_asset=lookalike or "", tx_hash=tx_hash,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "agent_wallet_monitor: failed to log to phishing_blacklist: %s", exc,
                        )

    return fresh


async def _attach_usd_values(other_tokens: list[dict[str, Any]], *, chain: str) -> None:
    """Adds ``price_usd``/``value_usd`` to each entry in ``other_tokens``
    (#204 follow-up, operator request: "if I buy Virtual or a small cap it
    should display with the token quantity and its $ value") -- reuses
    ``services/dexscreener.fetch_tokens_batch`` (already built, #194, up to
    30 addresses in a single call), never a duplicated new price client. If
    several pools exist for the same token, keeps the one with the highest
    liquidity (same heuristic as ``acp_onchain_scan.py``) -- the most
    reliable price, not the first one found. Mutates each entry in place;
    ``price_usd``/``value_usd`` stay ``None`` if the price can't be found,
    never an invented value."""
    for t in other_tokens:
        t["price_usd"] = None
        t["value_usd"] = None

    try:
        from aria_core.services.dexscreener import fetch_tokens_batch

        pairs = await fetch_tokens_batch([t["address"] for t in other_tokens], chain=chain)
    except Exception as exc:  # noqa: BLE001 -- a price failure never erases the already-known balance
        logger.warning("agent_wallet_monitor: token price lookup failed: %s", exc)
        return

    best_by_address: dict[str, Any] = {}
    for p in pairs:
        addr = (p.base_address or "").lower()
        if not addr or not p.price_usd:
            continue
        current = best_by_address.get(addr)
        if current is None or p.liquidity_usd > current.liquidity_usd:
            best_by_address[addr] = p

    for t in other_tokens:
        pair = best_by_address.get(t["address"].lower())
        if pair is None:
            continue
        t["price_usd"] = pair.price_usd
        t["value_usd"] = t["amount"] * pair.price_usd


async def get_wallet_balance_summary(
    *, wallet_address: str = "", chain: str = "base",
) -> dict[str, Any]:
    """REAL current balance of the agent wallet (#204, 07/16 follow-up:
    operator request "I want to see everything, even future purchased
    tokens") -- ALL tokens actually held via the CDP adapter
    (``list_all_token_balances``, same call already verified live for USDC,
    07/16, #157), native ETH via Blockscout (already built, already used
    elsewhere in ARIA -- read-only, no dependency on the CDP SDK). Each value
    honestly degrades to ``None`` if unavailable, never an invented balance --
    if the pilot ever swaps into a new token, it shows up here automatically,
    no list to maintain by hand."""
    wallet_address = wallet_address or MONITORED_WALLET_ADDRESS

    usdc: float | None = None
    other_tokens: list[dict[str, Any]] | None = None
    try:
        from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS, list_all_token_balances

        tokens = await list_all_token_balances(network=chain)
    except Exception as exc:  # noqa: BLE001 -- the cdp-sdk extra may be missing, never break the caller
        logger.warning("agent_wallet_monitor: token balance lookup failed: %s", exc)
        tokens = None

    cdp_eth_fallback: float | None = None
    if tokens is not None:
        usdc = 0.0
        other_tokens = []
        for t in tokens:
            if t["address"].lower() == USDC_BASE_ADDRESS.lower():
                usdc = t["amount"]
            elif (t.get("symbol") or "").strip().upper() == "ETH":
                # CDP list_token_balances also returns native ETH (confirmed live
                # on 07/16, /agentwallet) -- never an "other token" purchased,
                # used as a fallback if Blockscout fails, never shown twice.
                cdp_eth_fallback = t["amount"]
            else:
                other_tokens.append(t)
        if other_tokens:
            await _attach_usd_values(other_tokens, chain=chain)

    eth: float | None = None
    try:
        client = get_blockscout_client(chain)
        info = await client.get_address_info(wallet_address)
        if info.available:
            eth = info.balance_native
    except Exception as exc:  # noqa: BLE001 -- a Blockscout failure must never break the caller
        logger.warning("agent_wallet_monitor: ETH balance lookup failed: %s", exc)

    if eth is None and cdp_eth_fallback is not None:
        eth = cdp_eth_fallback

    return {
        "wallet_address": wallet_address, "chain": chain,
        "usdc_usd": usdc, "eth": eth, "other_tokens": other_tokens,
    }


def _x402_spend_may_have_settled(spend: dict) -> bool:
    """A logged x402 spend counts as a possible real on-chain payment for the
    purpose of ``_matches_known_x402`` even when its local ``status`` is
    ``"failed"``, IF the failure happened AFTER ``pay_fn`` already succeeded
    -- the signed authorization was handed to the provider, whose facilitator
    may have settled it on-chain even though this client's own follow-up
    request to fetch the resource then failed (network exception). Matches on
    ``x402_executor.REASON_PREFIX_PAID_BUT_FETCH_FAILED`` (a named constant,
    never a hand-copied string, so a future reword of the message can't
    silently break this).

    Real incident, 23/07: a twit.sh payment (0.01 USDC, fBOMB) settled
    on-chain but the local spend log recorded ``status="failed"`` because
    reading the paid response itself raised -- the monitor's classifier
    couldn't recognize it as ``known_x402`` and raised a false
    "unexpected_outflow" alert.

    Excludes every OTHER "failed" reason (``"signature échouée"`` -- `pay_fn`
    itself never returned a header, no money could have moved; ``"toujours
    402 après paiement"`` -- the facilitator explicitly REFUSED settlement,
    no money moved either): those failures correctly stay unmatched, same as
    before this fix. Fail-open here is safe, not a security weakening: at
    worst a genuinely-failed spend is recognized as ``known_x402`` instead of
    raising an alarm for a payment that in fact never completed -- the
    reverse (a real leak mistaken for x402) can't happen, since
    ``_matches_known_x402`` still requires the exact pay_to/amount/time-window
    match regardless of this broadened status filter."""
    from aria_core import x402_executor

    status = spend.get("status")
    if status == "ok":
        return True
    if status == "failed":
        return (spend.get("reason") or "").startswith(x402_executor.REASON_PREFIX_PAID_BUT_FETCH_FAILED)
    return False


async def _send_outflow_confirmation(m: WalletMovement) -> bool:
    """29/07 -- Item #187, redesigned by Item #198: turns a passive
    ``unexpected_outflow`` notification into an interactive Telegram
    confirmation (same approvals/callback pattern as ``wallet_guard.
    escalate_spend``, reused rather than duplicated). By the time this runs,
    the kill-switch is ALREADY armed (the caller, ``run_agent_wallet_
    monitor_cycle``, pauses immediately on detection, never waiting for this
    confirmation) -- "🚫 PAS autorisé" just confirms it should STAY paused,
    "✅ Autorisé par moi" is what actually LIFTS it (owner-only check, see
    ``_handle_callback``'s ``outflow_confirm:`` branch, gateway/telegram_bot.py).
    Returns ``False`` on ANY failure (missing admin_ids, bot not started,
    approval creation failed) so the caller falls back to a plain text
    notification -- the alert itself must never be lost even if this richer
    path breaks."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from aria_core import approvals
    from aria_core.gateway import telegram_bot

    if not telegram_bot.settings.admin_ids:
        return False

    payload = {
        "tx_hash": m.tx_hash, "wallet_name": m.wallet_name, "amount": m.amount,
        "asset": m.asset, "counterparty": m.counterparty,
    }
    try:
        req = await approvals.create_approval(
            action=f"outflow_confirm:{m.tx_hash}",
            description=format_movement_alert(m),
            payload=json.dumps(payload, ensure_ascii=False),
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Autorisé par moi", callback_data=f"approve:{req.id}"),
                    InlineKeyboardButton("🚫 PAS autorisé", callback_data=f"reject:{req.id}"),
                ],
            ]
        )
        text = (
            format_movement_alert(m)
            + "\n\n⛔ <b>ARIA est DÉJÀ en pause</b> (kill-switch armé automatiquement à la "
            "détection). Confirme si c'est bien toi : « Autorisé par moi » lève la pause, "
            "« PAS autorisé » la laisse active."
        )
        sent = await telegram_bot.send_message(
            text, telegram_bot.settings.admin_ids[0], parse_mode="HTML", reply_markup=keyboard,
        )
        return bool(sent)
    except Exception as exc:  # noqa: BLE001 -- any failure here degrades to plain notification
        logger.warning("agent_wallet_monitor: interactive outflow confirmation failed: %s", exc)
        return False


async def run_agent_wallet_monitor_cycle(*, notifier=None) -> dict:
    """One heartbeat tick: reads real movements on ALL monitored wallets
    (``MONITORED_WALLETS``, 07/23 -- extended from the single historical
    pilot wallet), immediately notifies each freshly detected movement (the
    kill-switch cuts the NOTIFICATION, never the reading/logging itself --
    even paused, ARIA must keep a full ledger, cf. operator request "full
    ledger ... so you can verify in real time"). Fail-closed PER WALLET: a
    failure on one wallet (e.g. invalid address, Blockscout unavailable for
    this specific request) never blocks surveillance of the others -- only
    if ALL fail AND no movement was found elsewhere does the cycle report
    "error".

    Item #188 (29/07): also snapshots the 4 critical capital-adjacent gates
    (x402 seller/mainnet, agent-wallet pilot/transfer) into ``gate_audit_
    log`` on every tick -- BEFORE the ``agent_wallet_monitor_enabled`` early
    return, deliberately: knowing what these gates were set to must never
    depend on whether wallet surveillance itself happens to be on (born from
    the 29/07 incident, where reconstructing gate state after the fact was
    impossible)."""
    try:
        from aria_core import gate_audit_log

        await gate_audit_log.snapshot_tracked_gates()
    except Exception as exc:  # noqa: BLE001 -- audit logging must never block the cycle
        logger.warning("agent_wallet_monitor: gate audit snapshot failed: %s", exc)

    if not agent_wallet_monitor_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import agent_wallet_log, custody_pause, x402_budget

    try:
        logged = await agent_wallet_log.list_transactions(limit=500)
    except Exception as exc:  # noqa: BLE001 -- a log failure must never block monitoring
        logged = []
        logger.warning("agent_wallet_monitor: agent_wallet_log lookup failed: %s", exc)
    known_tx_hashes = {row["tx_hash"] for row in logged if row.get("status") == "ok" and row.get("tx_hash")}

    try:
        x402_spends = await x402_budget.list_spends()
    except Exception as exc:  # noqa: BLE001 -- an x402 log failure must never block monitoring
        x402_spends = []
        logger.warning("agent_wallet_monitor: x402_budget lookup failed: %s", exc)
    known_x402_spends = [s for s in x402_spends if _x402_spend_may_have_settled(s) and s.get("pay_to")]

    movements: list[WalletMovement] = []
    errors: list[str] = []
    for wallet_name, wallet_address in MONITORED_WALLETS.items():
        try:
            wallet_movements = await check_wallet_activity(
                wallet_address=wallet_address, wallet_name=wallet_name,
                known_tx_hashes=known_tx_hashes, known_x402_spends=known_x402_spends,
            )
        except Exception as exc:  # noqa: BLE001 -- a failure on THIS wallet never blocks the others
            errors.append(f"{wallet_name}: {str(exc)[:200]}")
            logger.warning("agent_wallet_monitor: cycle failed for %s: %s", wallet_name, exc)
            continue
        movements.extend(wallet_movements)

    if not movements:
        if errors:
            return {"outcome": "error", "error": "; ".join(errors)[:300]}
        return {"outcome": "nothing_new"}

    # 07/23 -- explicit operator decision: the cycle runs in the background
    # (8h, cf. comment in heartbeat.py) without notifying every movement --
    # the operator already sees raw movements via Zerion, only ARIA does the
    # security classification. ONLY "unexpected_outflow" (outflow not
    # initiated by ARIA, possible sign of a compromised key) deserves an
    # immediate alert -- everything else (deposits, detected phishing, swaps,
    # x402 payments) stays silent, logged but never notified.
    #
    # 29/07 -- Item #198, post-incident (operator explicit request: "systeme
    # blinde... si ca arrive le compte bloque tout swap ou tout transfert"):
    # the kill-switch now arms IMMEDIATELY on detection, fail-closed, never
    # waiting on a human reply first (Item #187's confirmation flow used to
    # do the opposite -- "PAS autorisé" armed it, "Autorisé par moi" was a
    # no-op -- meaning nothing was blocked for however long the operator took
    # to respond). Never re-arms if ARIA is ALREADY paused (for this or any
    # other reason) -- pause() would just overwrite a possibly more relevant
    # existing reason. Notification is now sent REGARDLESS of the pause
    # state (dropped the old "if notifier and not paused" guard) -- an
    # already-paused ARIA still deserves to know a NEW incident occurred,
    # this classification was never gated behind that check for any other
    # reason than avoiding redundant confirmations under the OLD (reactive)
    # design.
    notified = 0
    if notifier:
        for m in movements:
            if m.classification != "unexpected_outflow":
                continue
            # Item #62 (08/03): arms the DEDICATED custody flag, never the
            # shared outgoing_pause -- a false positive here (as happened
            # live, a misclassified OpenRouter recharge) used to also
            # silently freeze paper trading (momentum_websocket.py's drain)
            # for hours, even though paper has zero custody surface. See
            # custody_pause.py's own module docstring for the full incident
            # and the two-workflow analysis behind this split.
            if not custody_pause.is_paused():
                pause_reason = (
                    f"Sortie non initiee par ARIA detectee automatiquement "
                    f"(wallet {m.wallet_name}, tx {m.tx_hash})"
                )
                custody_pause.pause(by="auto:agent_wallet_monitor", reason=pause_reason)
                try:
                    from aria_core import kill_incident_log

                    await kill_incident_log.record_incident(
                        event_type=kill_incident_log.EVENT_ARMED,
                        trigger_source=kill_incident_log.TRIGGER_AUTO,
                        by="auto:agent_wallet_monitor",
                        reason=pause_reason,
                        wallet_name=m.wallet_name,
                        tx_hash=m.tx_hash,
                    )
                except Exception as exc:  # noqa: BLE001 -- audit logging must never block the cycle
                    logger.warning("agent_wallet_monitor: kill incident logging failed: %s", exc)
            try:
                sent = await _send_outflow_confirmation(m)
                if not sent:
                    await notifier(format_movement_alert(m))
                notified += 1
            except Exception as exc:  # noqa: BLE001 -- a failed send must never lose the ledger entry
                logger.warning("agent_wallet_monitor: notification echouee pour %s: %s", m.tx_hash, exc)

    return {
        "outcome": "ok",
        "detected": len(movements),
        "notified": notified,
        "unexpected_outflows": sum(1 for m in movements if m.classification == "unexpected_outflow"),
    }


def basescan_tx_url(tx_hash: str) -> str:
    """Public BaseScan URL for a transaction -- pure construction (no network
    call), same pattern as `dexscreener.token_url`."""
    return f"https://basescan.org/tx/{tx_hash}"


def _html_link(text: str, url: str) -> str:
    """Telegram HTML link (``<a href="...">text</a>``) -- ``text`` escaped
    (may contain arbitrary on-chain data, e.g. a tx_hash or a token symbol),
    ``url`` built internally by ``basescan_tx_url``/``dexscreener.token_url``
    (never a URL supplied by a third party)."""
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


def format_movement_alert(m: WalletMovement) -> str:
    """HTML format (Telegram ``parse_mode="HTML"``, cf.
    ``heartbeat._notify_telegram_html``) -- 07/23, operator request: "every
    hash should link to basescan on click". ANY text field coming from
    external data (token asset/symbol, address, resource/provider) is escaped
    via ``html.escape`` before insertion -- a token symbol controlled by a
    malicious third party could otherwise inject HTML markup into the
    message."""
    icon = {
        "known": "✅", "external_deposit": "💰", "unexpected_outflow": "🚨",
        "suspicious_token": "🎣", "known_x402": "🧾", "swap": "🔄",
        "internal_transfer": "🏠",
    }.get(m.classification, "•")
    label = {
        "known": "Mouvement initié par ARIA (attendu)",
        "external_deposit": "Dépôt externe détecté",
        "unexpected_outflow": "SORTIE NON INITIÉE PAR ARIA — à vérifier immédiatement",
        "suspicious_token": "TOKEN SUSPECT — imite un actif suivi, PAS un dépôt réel, ne jamais interagir avec ce contrat",
        "known_x402": "Paiement x402 initié par ARIA (attendu)",
        "swap": "Swap détecté",
        "internal_transfer": "Transfert entre wallets ARIA/opérateur (aucune alerte)",
    }.get(m.classification, m.classification)
    # 07/23 -- multi-wallet: name of the wallet involved at the top of the
    # alert, empty -> "Wallet agent" (unchanged historical behavior for any
    # movement built without this field).
    wallet_label = html.escape(m.wallet_name or "Wallet agent")
    tx_link = _html_link(m.tx_hash, basescan_tx_url(m.tx_hash))
    if m.classification == "swap":
        lines = [
            f"{icon} {wallet_label} — {html.escape(label)}",
            f"Swap : {m.amount_out} {html.escape(m.asset_out)} -> {m.amount} {html.escape(m.asset)}",
            f"Contrepartie : {html.escape(_label_address(m.counterparty))}",
            f"Tx : {tx_link}",
        ]
        return "\n".join(lines)
    direction_label = "Entrée" if m.direction == "in" else "Sortie"
    counterparty_label = "De" if m.direction == "in" else "Vers"
    lines = [
        f"{icon} {wallet_label} — {html.escape(label)}",
        f"{direction_label} : {m.amount} {html.escape(m.asset)}",
        f"{counterparty_label} : {html.escape(_label_address(m.counterparty))}",
        f"Tx : {tx_link}",
    ]
    # 29/07 -- Item #187: the on-chain method name, shown right after the
    # basics -- previously only discoverable by digging into Blockscout by
    # hand after the fact (real friction hit during the 29/07 stress-test
    # investigation). Shown whenever known, not just for unexpected_outflow --
    # cheap and never misleading for any classification.
    if m.method:
        lines.append(f"Méthode : {html.escape(m.method)}")
    # 07/22 -- enrichment ONLY for an x402 payment whose matched spend could be
    # tied to a specific token (m.contract): a generic x402 payment (e.g. web
    # search) has no contract, soft degradation = nothing added at all, never
    # an empty line or "N/A".
    # 07/23 -- separate "BaseScan :" line removed (redundant: the tx_hash
    # above is now itself clickable).
    if m.classification == "known_x402" and m.contract:
        # 07/22 (cross-review) -- token_symbol shown next to the address when known
        # (e.g. "GEM (0xdead...)") rather than left dead after being captured/propagated.
        token_line = (
            f"Token : {html.escape(m.token_symbol)} ({html.escape(m.contract)})"
            if m.token_symbol else f"Token : {html.escape(m.contract)}"
        )
        lines.append(token_line)
        lines.append(f"DexScreener : {_html_link(m.contract, token_url(m.contract, chain='base'))}")
        # 07/22 (cross-review) -- explicit guard: x402_budget.record_spend() requires
        # `resource` as a mandatory parameter (never empty in practice), but without
        # this guard corrupted data would produce a half-empty "Raison : " line,
        # contrary to the "never an empty line" doctrine already applied just above.
        if m.resource:
            resource = html.escape(m.resource)
            provider = html.escape(m.provider) if m.provider else ""
            lines.append(f"Raison : {resource} via {provider}" if provider else f"Raison : {resource}")
    return "\n".join(lines)


def format_wallet_balance_summary(summary: dict[str, Any]) -> str:
    """Formats the result of ``get_wallet_balance_summary`` for Telegram
    (#204 follow-up: "I want to see everything, even future purchased
    tokens") -- each unavailable balance is honestly displayed as such,
    never a silent 0 that would suggest an empty wallet. Any other token
    held (e.g. after a future pilot swap) displays automatically, no list
    to maintain by hand."""
    usdc = summary.get("usdc_usd")
    eth = summary.get("eth")
    other_tokens = summary.get("other_tokens")
    usdc_line = f"{usdc:.4f} USDC" if usdc is not None else "indisponible (SDK/identifiants CDP absents)"
    eth_line = f"{eth:.6f} ETH" if eth is not None else "indisponible (Blockscout hors service)"
    lines = [
        f"💼 Wallet agent CDP ({summary.get('chain', 'base')})",
        f"Adresse : {summary.get('wallet_address', '?')}",
        f"USDC : {usdc_line}",
        f"ETH (gas) : {eth_line}",
    ]
    if other_tokens is None:
        lines.append("Autres tokens : indisponible (SDK/identifiants CDP absents)")
    elif other_tokens:
        lines.append("Autres tokens :")
        for t in other_tokens:
            value_usd = t.get("value_usd")
            value_label = f"(~{value_usd:,.2f} $)" if value_usd is not None else "(prix indisponible)"
            lines.append(f"  - {t['amount']} {t['symbol']} {value_label}")
    else:
        lines.append("Autres tokens : aucun")
    return "\n".join(lines)
