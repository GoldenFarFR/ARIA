"""Quick Intel client (x402) -- token security scan, $0.03/call.

Diligenced 30/07 after a real discrepancy found live on PONKE: a Quick Intel
DexScreener widget claimed "Has blacklist: Yes" for a contract with zero
blacklist logic in its real, verified source (a false POSITIVE), while
GoPlus had missed a genuine callable ``mint()`` on the same contract (a
false NEGATIVE). Neither scanner alone is trustworthy on these pattern-based
flags -- see ``skills/source_code_audit.py`` (Item #234) for the
arbitration mechanism this cross-checks against.

Real 402 challenge captured live (30/07, no payment made) against
``https://x402.quickintel.io/v1/scan/full``: a POST with a JSON body
(``{"chain", "tokenAddress"}``) is required on the identical request both
before and after payment (unlike every other x402 provider integrated so
far, which identify the resource via the URL alone) -- ``fetch_paid_resource``
gained a ``json_body`` param for this. The offer is a full x402 v2 envelope
present in BOTH the JSON body and the ``payment-required`` header at once --
a real bug (fixed the same day in ``x402_executor._extract_payment_requirement``)
made this class of offer fail signing 100% of the time before this client
existed, since the header carrying the info the SDK actually needs for v2
was silently dropped whenever the body path resolved first.

Informational cross-check only, gated OFF by default -- never a decision
input on its own until a real batch of results is reviewed."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SCAN_URL = "https://x402.quickintel.io/v1/scan/full"


def quickintel_enabled() -> bool:
    return os.environ.get("ARIA_QUICKINTEL_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def scan_full(chain: str, token_address: str) -> dict[str, Any]:
    """Full token security scan (x402, $0.03) -- honeypot, tax, ownership,
    mint/blacklist/pause, proxy detection, LP lock, scam-wallet funding.
    Same dome as every other x402 client here: never an exception bubbling
    up, ``available=False`` on any failure (gate off, budget exhausted,
    insufficient balance, unreadable response)."""
    if not quickintel_enabled():
        return {"available": False, "raw": None, "error": "gate désactivé", "amount_usd": 0.0}

    chain = (chain or "").strip().lower()
    token_address = (token_address or "").strip()
    if not chain or not token_address:
        return {"available": False, "raw": None, "error": "chain/tokenAddress vide", "amount_usd": 0.0}

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    result = await x402_executor.fetch_paid_resource(
        _SCAN_URL,
        resource="scan-full",
        provider="quickintel",
        method="POST",
        balance_fn=usdc_balance_usd,
        pay_fn=build_x402_payment_header,
        json_body={"chain": chain, "tokenAddress": token_address},
        contract=token_address,
        timeout=20.0,  # payment settlement + a full contract audit, slower than a plain lookup
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
