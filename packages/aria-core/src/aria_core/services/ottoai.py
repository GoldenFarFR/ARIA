"""Client Otto AI (x402) — digest crypto-Twitter payant (0,001 $/appel, le moins
cher testé du catalogue x402 Bazaar, 19/07). Repéré via x402scan.com (captures
opérateur), testé en conditions réelles à 2 reprises le même soir (contenu frais
et différent à 48 min d'intervalle -- confirmé RÉEL et à jour, pas un cache
statique).

Fournit un digest GÉNÉRAL de marché (alertes critiques, activité whale/
institutionnelle, DeFi, autre actu) -- PAS un signal par-projet. À distinguer de
`conviction_research.py` (X par ticker/contrat via twit.sh, #111, décision séparée).

Chaque appel passe par `x402_executor.fetch_paid_resource` (plafond hebdo
`x402_budget`, coupe-circuit `/stop`, wallet CDP dédié) -- aucune clé, aucun
paiement construit ici, ce module ne fait qu'appeler et parser la réponse. Même
dôme que `services/cybercentry.py`."""
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
    """Récupère le digest crypto-Twitter général du moment (Otto AI, x402, 0,001$).
    Jamais une exception qui remonte -- même dôme que le reste des clients externes."""
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
    except Exception as exc:  # noqa: BLE001 — honore le contrat "jamais une exception qui remonte"
        return OttoAIDigest(available=False, error=f"appel x402 échoué ({exc})")
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
    except Exception as exc:  # noqa: BLE001 — corps illisible, jamais une exception qui remonte
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
