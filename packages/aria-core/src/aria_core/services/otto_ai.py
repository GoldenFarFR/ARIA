"""Otto AI client (x402) -- two secondary, informational-only market signals
found in a full x402 Bazaar scan (07/24): Hyperliquid perpetuals data
($0.001/call) and TradFi macro intelligence ($0.003/call). Both from a
provider ARIA already trusts and pays (twit.sh's x402 sibling ecosystem is
distinct, but Otto AI itself is the best-proven provider found in the whole
derivatives/macro batch -- 1127 and 1336 calls/30d respectively, cf. HANDOFF).

Real 402 challenge shape verified live (07/24, no payment made -- a bare GET
to an x402 endpoint only returns the payment-required challenge, it never
charges anything): both endpoints return a small JSON body
(error/hint/price/docs) plus the real x402 v2 payment offer in the
PAYMENT-REQUIRED header, exactly like Cybercentry -- same
``x402_executor.fetch_paid_resource`` handles the whole flow, no new
payment logic needed here.

Cross-check only, NEVER a decision input: market_sentiment.py's own
deterministic BTC/ETH engine remains the sole source for Regime Switch.
Gated OFF by default -- wiring these into any pipeline is a separate,
explicit step, not assumed here."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_HYPERLIQUID_URL = "https://x402.ottoai.services/hyperliquid-market"
_TRADFI_URL = "https://x402.ottoai.services/tradfi-data"


def otto_ai_enabled() -> bool:
    return os.environ.get("ARIA_OTTO_AI_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def _fetch(url: str, *, resource: str) -> dict[str, Any]:
    """Same dome/shape as cybercentry.verify_wallet -- never an exception
    bubbling up, ``available=False`` on any failure (gate off, budget
    exhausted, unreadable response)."""
    if not otto_ai_enabled():
        return {"available": False, "raw": None, "error": "gate désactivé", "amount_usd": 0.0}

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    result = await x402_executor.fetch_paid_resource(
        url, resource=resource, provider="otto_ai",
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


async def hyperliquid_market_data() -> dict[str, Any]:
    """Broader perpetuals market funding-rate/leverage signal (x402, $0.001) --
    a leverage/sentiment cross-check the BTC/ETH-only Regime Switch doesn't
    have. Informational only."""
    return await _fetch(_HYPERLIQUID_URL, resource="hyperliquid-market")


async def tradfi_macro_data() -> dict[str, Any]:
    """TradFi macro intelligence (VIX/DXY/rates, x402, $0.003) -- a secondary
    macro cross-check for market_sentiment.py. Informational only."""
    return await _fetch(_TRADFI_URL, resource="tradfi-data")
