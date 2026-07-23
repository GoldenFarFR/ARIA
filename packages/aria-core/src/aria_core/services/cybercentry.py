"""Cybercentry client (x402) — paid security checks (0.02 $/call).

Complements GoPlus (free honeypot guard) with a paid second opinion when
useful (#199, 17/07, operator decision: pay for whatever feeds the vector
memory the most). Real endpoints, verified live on 17/07 against
https://cybercentry.gitbook.io/cybercentry/documents/x402-cybercentry —
`ethereum-token-verification` was down (502 Railway) at test time,
`wallet-verification` responds correctly, wired first here. The other
endpoints (Solidity/Solana/token) follow the same pattern if needed someday.
Retested on 17/07 (next session): `ethereum-token-verification` still
unavailable -- a different failure mode this time (TCP/TLS open, request
sent, no response, timeout after 15s -- not an immediate 502), while
`wallet-verification` still responds correctly (402) on the same Railway
infra at the same instant. Confirms this is neither a network/allowlist
block on ARIA's side nor a global Cybercentry outage -- this specific
service stays broken. Do not build `verify_and_remember_token` before a
new test confirms a real response (402 or 200), so as not to code against
an unreachable API.

Every call goes through `x402_executor.fetch_paid_resource` (weekly cap
`x402_budget`, `/stop` kill-switch, dedicated CDP wallet) -- no key, no
payment built here, this module only calls and parses the response."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_WALLET_VERIFICATION_URL = "https://x402-cybercentry-wallet-verification.up.railway.app/verify"


async def verify_wallet(address: str) -> dict[str, Any]:
    """Checks a wallet address (sanctions/fraud/risk) via Cybercentry (x402,
    0.02 $). Returns ``{"available": bool, "raw": dict|None, "error": str|None,
    "amount_usd": float}`` -- never an exception bubbling up (same dome as the
    rest of the external clients)."""
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    addr = (address or "").strip()
    if not addr:
        return {"available": False, "raw": None, "error": "adresse vide", "amount_usd": 0.0}

    result = await x402_executor.fetch_paid_resource(
        f"{_WALLET_VERIFICATION_URL}?data={addr}",
        resource="wallet-verification",
        provider="cybercentry",
        balance_fn=usdc_balance_usd,
        pay_fn=build_x402_payment_header,
    )
    if result.status != "ok":
        return {
            "available": False, "raw": None,
            "error": result.reason or f"status={result.status}",
            "amount_usd": result.amount_usd,
        }
    try:
        raw = json.loads(result.body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — unreadable body, never an exception bubbling up
        return {
            "available": False, "raw": None, "error": f"réponse illisible ({exc})",
            "amount_usd": result.amount_usd,
        }
    return {"available": True, "raw": raw, "error": None, "amount_usd": result.amount_usd}
