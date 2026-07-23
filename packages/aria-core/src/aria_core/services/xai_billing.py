"""Read-only x.ai Management API client — prepaid balance (billing), NOT
inference (services/../llm.py already covers inference, separate key).

Verified at the source (docs.x.ai/developers/rest-api-reference/management/billing,
18/07) before wiring: dedicated base URL `https://management-api.x.ai`,
endpoint `GET /v1/billing/teams/{team_id}/prepaid/balance`, response
`{"total": {"val": "<cents str, negative = credit available>"}, "changes": [...]}`.
Requires a "Management" key distinct from the classic inference key
(`GROK_API_KEY`) -- `XAI_MANAGEMENT_KEY` + `XAI_TEAM_ID`, both empty until
the operator has generated them on console.x.ai. Explicit `available=False`
in that case, never a fabricated balance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from aria_core.runtime import settings

logger = logging.getLogger(__name__)

MANAGEMENT_BASE_URL = "https://management-api.x.ai"

UNAVAILABLE = "x.ai balance unavailable"


def _management_key() -> str:
    return (getattr(settings, "xai_management_key", "") or "").strip()


def _team_id() -> str:
    return (getattr(settings, "xai_team_id", "") or "").strip()


def xai_billing_configured() -> bool:
    return bool(_management_key() and _team_id())


@dataclass
class XaiBalance:
    balance_usd: float | None = None
    available: bool = False
    error: str | None = None


async def get_prepaid_balance() -> XaiBalance:
    """Queries the real prepaid balance. Best-effort, never blocking: a
    network failure or missing credentials fall back to `available=False` +
    `error`, never a guessed balance."""
    key = _management_key()
    team_id = _team_id()
    if not key or not team_id:
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (XAI_MANAGEMENT_KEY/XAI_TEAM_ID missing)")

    url = f"{MANAGEMENT_BASE_URL}/v1/billing/teams/{team_id}/prepaid/balance"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.TransportError as exc:
        logger.warning("xai_billing: network failure (%s)", exc)
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (network: {exc})")

    if response.status_code != 200:
        logger.warning("xai_billing: HTTP %s -> %s", response.status_code, response.text[:300])
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

    try:
        data = response.json()
        cents = float(data["total"]["val"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("xai_billing: unexpected response (%s)", exc)
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (unexpected response format)")

    # Doc: "negative indicates credits available" -- a positive prepaid
    # balance for the operator translates to a NEGATIVE value on the API
    # side (debt-style account).
    balance_usd = -cents / 100.0
    return XaiBalance(balance_usd=balance_usd, available=True, error=None)
