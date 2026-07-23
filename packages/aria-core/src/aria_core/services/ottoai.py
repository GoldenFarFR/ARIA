"""Otto AI client (x402) — paid crypto-Twitter digest (0.001 $/call, the cheapest
tested in the x402 Bazaar catalog, 07/19). Spotted via x402scan.com (operator
screenshots), tested under real conditions twice the same evening (fresh and
different content 48 min apart -- confirmed REAL and up to date, not a static
cache).

Provides a GENERAL market digest (critical alerts, whale/institutional
activity, DeFi, other news) -- NOT a per-project signal. To be distinguished from
`conviction_research.py` (X by ticker/contract via twit.sh, #111, a separate decision).

Every call goes through `x402_executor.fetch_paid_resource` (weekly cap
`x402_budget`, `/stop` kill-switch, dedicated CDP wallet) -- no key, no
payment built here, this module only calls and parses the response. Same
guardrail as `services/cybercentry.py`."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TWITTER_SUMMARY_URL = "https://x402.ottoai.services/twitter-summary"


@dataclass(frozen=True)
class OttoAIDigest:
    available: bool
    digest_text: str = ""
    timestamp: str | None = None
    error: str | None = None
    amount_usd: float = 0.0


async def fetch_twitter_digest() -> OttoAIDigest:
    """Fetches the current general crypto-Twitter digest (Otto AI, x402, 0.001$).
    Never a bubbling exception -- same guardrail as the rest of the external clients."""
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    try:
        result = await x402_executor.fetch_paid_resource(
            _TWITTER_SUMMARY_URL,
            resource="twitter-summary",
            provider="ottoai",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
        )
    except Exception as exc:  # noqa: BLE001 -- honors the "never a bubbling exception" contract
        return OttoAIDigest(available=False, error=f"x402 call failed ({exc})")
    if result.status != "ok":
        return OttoAIDigest(
            available=False, error=result.reason or f"status={result.status}",
            amount_usd=result.amount_usd,
        )
    try:
        raw = json.loads(result.body.decode("utf-8"))
        data = raw.get("data") or {}
        digest_text = str(data.get("digest") or "").strip()
        timestamp = data.get("timestamp")
    except Exception as exc:  # noqa: BLE001 -- unreadable body, never a bubbling exception
        return OttoAIDigest(
            available=False, error=f"réponse illisible ({exc})", amount_usd=result.amount_usd,
        )
    if not digest_text:
        return OttoAIDigest(
            available=False, error="digest vide dans la réponse", amount_usd=result.amount_usd,
        )
    return OttoAIDigest(
        available=True, digest_text=digest_text,
        timestamp=timestamp if isinstance(timestamp, str) else None,
        amount_usd=result.amount_usd,
    )
