"""Real-order execution adapter for Polymarket's CLOB (Polygon), built 08/03
as part of the diligence for a possible future real-money Polymarket pilot
(distinct from the paper-trading pipeline in ``polymarket_paper_trader.py``,
which never touches this module).

**Real capital is NOT enabled by this module today.** Two independent
conditions, both external to this code, gate any future activation (operator
decisions, CLAUDE.md "Regles absolues"):
  1. The operator's planned relocation (Andorra) must be effective, not just
     announced.
  2. A lawyer must confirm the real legal status there -- Andorra's own
     online-gambling licensing regime (Law 14/2024) is not yet activated by
     its regulator (CRAJ), a genuine grey area, not a confirmed green light.
Until both hold, ``ARIA_POLYMARKET_REAL_TRADING_ENABLED`` stays unset in
every environment this code runs in -- ``post_signed_order`` fails closed
unconditionally, structurally, not just "for now".

**SDK correction (08/03 diligence, real gap found and avoided)**: the
commonly-cited `py-clob-client` package is ARCHIVED (11/05/2026) and only
speaks Polymarket's V1 order protocol, abandoned by the exchange on the
28/04/2026 CLOB V2 cutover -- any order built with it would be rejected
("invalid order version"). This module uses `py_clob_client_v2` instead
(optional extra `aria-core[polymarket_execution]`), imported lazily so the
rest of the codebase is unaffected if the extra isn't installed (same
doctrine as `agent_wallet_cdp_adapter.py`'s lazy `cdp` import).

**No real testnet exists for Polymarket** (verified in the same diligence --
the official migration doc explicitly recommends testing with "a small
funded wallet, in production", no sandbox). The only safe dry-run this
module can offer is therefore structural: build + EIP-712-sign an order
locally (``build_signed_order``) without ever calling ``post_order`` on it --
this proves the order shape/signature is well-formed against the real SDK,
never that the exchange would fill it. Never conflate the two.

Wallet: reads a private key from ``POLYMARKET_WALLET_PRIVATE_KEY`` (own
dedicated Polygon wallet, never generated or stored by this code, never
mixed with the Base-only CDP agent-wallet pilot's own wallet -- same
isolation doctrine as every other real-money pilot in this project).

**Known gaps, found by a 08/03 security-review workflow, deliberately left
open because this module stays structurally inert today (no other module
imports it, the gate is never set anywhere) -- MUST be closed before any
future real activation is even considered, not before this commit:**
  - The hard cap (``MAX_BET_USD``) is checked against ``SignedOrderResult``'s
    own ``price``/``size`` fields, never against the actually-signed payload
    (``order.signed_order``) -- not exploitable via the one legitimate caller
    today (``build_signed_order`` builds both coherently), but the type
    doesn't itself guarantee it.
  - No check against the wallet's REAL balance (`agent_wallet_pilot.py`'s own
    doctrine: "hard cap checked against the wallet's REAL balance before
    every attempt, fail-closed if unavailable") -- only the fixed
    ``MAX_BET_USD`` is enforced here.
  - No persistent, queryable ledger (``agent_wallet_log.record_transaction``'s
    equivalent) -- traceability is `logging`-only today.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_REAL_MONEY_LOG_PREFIX = "[REAL MONEY] polymarket execution"

# 08/03, SAME-DAY correction: the addresses below were first sourced from
# Polymarket/agent-skills' SKILL.md, which turned out to still describe the
# pre-CLOB-V2 (28/04/2026) contracts -- USDC.e as collateral, the OLD CTF
# Exchange/Neg Risk Exchange. Operator flagged this live ("polymarket utilise
# polygon et usdc.e"), re-verified directly against docs.polymarket.com/
# resources/contracts (the platform's own "single source of truth" page) --
# every address below was wrong. Same class of gap as the archived
# py-clob-client SDK found earlier the same session: a source that LOOKS
# authoritative (an official Polymarket repo) can still lag the exchange's
# own live upgrades.
#
# Collateral is no longer USDC.e (or native USDC) at all -- Polymarket
# migrated to its own token, pUSD ("Polymarket USD"), a standard ERC-20 on
# Polygon backed 1:1 by USDC (the contract enforces the peg; 1 pUSD always
# converts back to 1 USDC). This is the proxy address (the one that matters
# for balance/allowance calls); docs also list an implementation address
# (0x6bBCef9f7ef3B6C592c99e0f206a0DE94Ad0925f) that callers never interact
# with directly.
PUSD_POLYGON_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

# CTF Exchange contract (V2, current) -- the allowance target for a standard
# (non neg-risk) market. EOA wallets must set this allowance before any
# order can fill.
CTF_EXCHANGE_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
# Neg-Risk Exchange contract (V2, current) -- a different allowance target
# for neg-risk markets (grouped multi-outcome events, e.g. the "what will
# happen before X" pattern already handled defensively in
# polymarket_paper_trader.py).
NEG_RISK_EXCHANGE_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"

# Placeholder, NOT an operator-validated figure -- no real amount has been
# discussed for a Polymarket pilot specifically (unlike the Base agent-wallet
# pilot's own $10-15 cap). Deliberately conservative and irrelevant in
# practice today: `post_signed_order` fails closed before this cap is ever
# checked, since the dedicated gate below has never been set anywhere.
MAX_BET_USD = 10.0


def polymarket_real_trading_enabled() -> bool:
    """Dedicated, separate gate -- OFF unless explicitly set. Never wired to
    any deploy config today; both preconditions in this module's docstring
    must hold before the operator would even consider setting it."""
    return os.environ.get("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class SignedOrderResult:
    """Result of building + signing an order locally -- never posted unless
    the caller explicitly goes on to call ``post_signed_order``."""
    token_id: str
    side: str
    price: float
    size: float
    signed_order: Any


def _require_sdk() -> Any:
    try:
        from py_clob_client_v2.client import ClobClient  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover -- exercised only without the optional extra
        raise RuntimeError(
            "py_clob_client_v2 n'est pas installe -- extra optionnel "
            "'aria-core[polymarket_execution]', jamais une dependance de base."
        ) from exc
    return ClobClient


async def build_signed_order(
    *, token_id: str, side: str, price: float, size: float, private_key: str,
) -> SignedOrderResult:
    """Builds and EIP-712-signs an order LOCALLY, never posts it -- the only
    safe dry-run Polymarket's own infrastructure allows (no testnet exists,
    see module docstring). Proves the order is well-formed against the real,
    current (V2) SDK; proves nothing about whether the exchange would fill
    it, which only ``post_signed_order`` (real capital, still gated off
    today) can ever confirm.

    ``private_key`` is never logged, stored, or echoed back by this
    function -- caller's responsibility to source it from an environment
    variable, same doctrine as every other wallet adapter in this project."""
    ClobClient = _require_sdk()
    from py_clob_client_v2.clob_types import OrderArgsV2  # type: ignore[import-not-found]

    client = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=private_key)
    order_args = OrderArgsV2(token_id=token_id, price=price, size=size, side=side)
    signed = client.create_order(order_args)
    logger.info(
        "%s: signed order built (never posted) token_id=%s side=%s price=%s size=%s",
        _REAL_MONEY_LOG_PREFIX, token_id, side, price, size,
    )
    return SignedOrderResult(token_id=token_id, side=side, price=price, size=size, signed_order=signed)


async def post_signed_order(order: SignedOrderResult, *, private_key: str) -> dict:
    """Posts a previously-built signed order to the real Polymarket exchange
    -- REAL CAPITAL. Fails closed, unconditionally, unless
    ``polymarket_real_trading_enabled()`` is true -- which today requires an
    env var never set in any environment this code runs in (see module
    docstring for the two external preconditions). Same doctrine as
    ``agent_wallet_pilot.attempt_swap``: dedicated gate checked FIRST, before
    the kill-switch, before the hard cap -- the narrowest, most critical
    gate goes first."""
    if not polymarket_real_trading_enabled():
        logger.critical(
            "%s: post_signed_order refused -- ARIA_POLYMARKET_REAL_TRADING_ENABLED not set",
            _REAL_MONEY_LOG_PREFIX,
        )
        return {"status": "blocked", "reason": "gate_disabled"}

    from aria_core import custody_pause, outgoing_pause

    if outgoing_pause.is_paused(strict=True):
        logger.warning("%s: post_signed_order refused -- kill-switch /stop active", _REAL_MONEY_LOG_PREFIX)
        return {"status": "blocked", "reason": "kill_switch_active"}

    # 08/03, security-review finding (workflow audit): custody_pause is the
    # AUTO-armed kill-switch (tripped by an anomaly detector, e.g.
    # agent_wallet_monitor.py), distinct from outgoing_pause's manual /stop --
    # custody_pause.py's own docstring requires checking it "alongside, never
    # instead of" outgoing_pause on every real-capital path. Missing here
    # would mean an anomaly auto-armed elsewhere never stops THIS wallet.
    if custody_pause.is_paused():
        logger.warning("%s: post_signed_order refused -- custody_pause active", _REAL_MONEY_LOG_PREFIX)
        return {"status": "blocked", "reason": "custody_pause_active"}

    notional_usd = order.price * order.size
    if notional_usd > MAX_BET_USD:
        logger.warning(
            "%s: post_signed_order refused -- notional %.2f$ > hard cap %.2f$",
            _REAL_MONEY_LOG_PREFIX, notional_usd, MAX_BET_USD,
        )
        return {"status": "blocked", "reason": "over_hard_cap"}

    ClobClient = _require_sdk()
    client = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=private_key)
    try:
        result = client.post_order(order.signed_order)
    except Exception:
        # 08/03, security-review finding: an exception AFTER the order may
        # already have reached the exchange must never vanish silently --
        # logged CRITICAL (same severity as a successful post) precisely
        # because the real-world outcome is unknown at this point, not
        # because it's necessarily a failure.
        logger.critical(
            "%s: post_order raised -- REAL ORDER STATUS UNKNOWN token_id=%s side=%s notional=%.2f$",
            _REAL_MONEY_LOG_PREFIX, order.token_id, order.side, notional_usd,
        )
        raise
    logger.critical(
        "%s: REAL ORDER POSTED token_id=%s side=%s notional=%.2f$",
        _REAL_MONEY_LOG_PREFIX, order.token_id, order.side, notional_usd,
    )
    return {"status": "posted", "result": result}
