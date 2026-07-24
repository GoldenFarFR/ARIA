"""BlockRun.AI Kalshi markets client (x402, $0.0095/call) -- found in a full
x402 Bazaar scan (07/24): Kalshi (CFTC-regulated prediction market exchange)
has zero coverage today in ARIA's macro/event context (polymarket.py only
covers Polymarket). Coinbase-curated, best-proven prediction-market item
found in the whole scan (427 calls/20 unique payers, cf. HANDOFF).

Real 402 challenge shape verified live (07/24, no payment made -- a bare GET
only returns the payment-required challenge, never charges anything):
{"error": "Payment Required", ..., "paymentInfo": {"network": "base",
"asset": "USDC", "x402Version": 2}} -- standard x402 v2, same
``x402_executor.fetch_paid_resource`` flow as every other provider here.

Strictly informational/contextual, same doctrine as polymarket.py -- never a
trading trigger. Gated OFF by default."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_KALSHI_MARKETS_URL = "https://blockrun.ai/api/v1/pm/kalshi/markets"


def blockrun_kalshi_enabled() -> bool:
    return os.environ.get("ARIA_BLOCKRUN_KALSHI_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def kalshi_markets() -> dict[str, Any]:
    """Kalshi markets (x402, $0.0095) -- same dome/shape as the other x402
    clients, never an exception bubbling up. Informational only, never a
    trading trigger."""
    if not blockrun_kalshi_enabled():
        return {"available": False, "raw": None, "error": "gate désactivé", "amount_usd": 0.0}

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    result = await x402_executor.fetch_paid_resource(
        _KALSHI_MARKETS_URL, resource="kalshi-markets", provider="blockrun_ai",
        balance_fn=usdc_balance_usd, pay_fn=build_x402_payment_header,
    )
    if result.status != "ok":
        return {
            "available": False, "raw": None,
            "error": result.reason or f"status={result.status}",
            "amount_usd": result.amount_usd,
        }
    try:
        raw = json.loads(result.body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 -- unreadable body, never an exception bubbling up
        return {
            "available": False, "raw": None, "error": f"réponse illisible ({exc})",
            "amount_usd": result.amount_usd,
        }
    return {"available": True, "raw": raw, "error": None, "amount_usd": result.amount_usd}
