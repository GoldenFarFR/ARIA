"""Client de lecture seule x.ai Management API — solde prépayé (billing), PAS
inférence (services/../llm.py couvre déjà l'inférence, clé distincte).

Vérifié à la source (docs.x.ai/developers/rest-api-reference/management/billing,
18/07) avant câblage : base URL dédiée `https://management-api.x.ai`, endpoint
`GET /v1/billing/teams/{team_id}/prepaid/balance`, réponse `{"total": {"val": "<cents
str, négatif = crédit dispo>"}, "changes": [...]}`. Exige une clé "Management"
distincte de la clé d'inférence classique (`GROK_API_KEY`) -- `XAI_MANAGEMENT_KEY` +
`XAI_TEAM_ID`, tous deux vides tant que l'opérateur ne les a pas générés sur
console.x.ai. `available=False` explicite dans ce cas, jamais un solde inventé.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from aria_core.runtime import settings

logger = logging.getLogger(__name__)

MANAGEMENT_BASE_URL = "https://management-api.x.ai"

UNAVAILABLE = "solde x.ai indisponible"


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
    """Interroge le solde prépayé réel. Best-effort, jamais bloquant : une panne
    réseau ou des identifiants absents retombent sur `available=False` + `error`,
    jamais un solde deviné."""
    key = _management_key()
    team_id = _team_id()
    if not key or not team_id:
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (XAI_MANAGEMENT_KEY/XAI_TEAM_ID absents)")

    url = f"{MANAGEMENT_BASE_URL}/v1/billing/teams/{team_id}/prepaid/balance"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.TransportError as exc:
        logger.warning("xai_billing: échec réseau (%s)", exc)
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (réseau: {exc})")

    if response.status_code != 200:
        logger.warning("xai_billing: HTTP %s -> %s", response.status_code, response.text[:300])
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

    try:
        data = response.json()
        cents = float(data["total"]["val"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("xai_billing: réponse inattendue (%s)", exc)
        return XaiBalance(available=False, error=f"{UNAVAILABLE} (format de réponse inattendu)")

    # Doc: "negative indicates credits available" -- un solde prépayé positif pour
    # l'opérateur se traduit par une valeur NÉGATIVE côté API (compte de type dette).
    balance_usd = -cents / 100.0
    return XaiBalance(balance_usd=balance_usd, available=True, error=None)
