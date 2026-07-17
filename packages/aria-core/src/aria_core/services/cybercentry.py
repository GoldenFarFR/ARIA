"""Client Cybercentry (x402) — vérifications de sécurité payantes (0,02 $/appel).

Complète GoPlus (garde-fou honeypot gratuit) par un second avis payant quand
c'est utile (#199, 17/07, décision opérateur : payer ce qui alimente le plus
la mémoire vectorielle). Endpoints réels, vérifiés en direct le 17/07 contre
https://cybercentry.gitbook.io/cybercentry/documents/x402-cybercentry —
`ethereum-token-verification` était en panne (502 Railway) au moment du test,
`wallet-verification` répond correctement, câblé en premier ici. Les autres
endpoints (Solidity/Solana/token) suivent le même patron si besoin un jour.
Retesté le 17/07 (session suivante) : `ethereum-token-verification` toujours
indisponible -- mode de panne différent cette fois (TCP/TLS ouvert, requête
envoyée, aucune réponse, timeout après 15s -- pas un 502 immédiat), pendant que
`wallet-verification` répond toujours correctement (402) sur la même infra
Railway au même instant. Confirme que ce n'est ni un blocage réseau/allowlist
côté ARIA ni une panne Cybercentry globale -- ce service précis reste cassé.
Ne pas construire `verify_and_remember_token` avant qu'un nouveau test confirme
une vraie réponse (402 ou 200), pour ne pas coder contre une API injoignable.

Chaque appel passe par `x402_executor.fetch_paid_resource` (plafond hebdo
`x402_budget`, coupe-circuit `/stop`, wallet CDP dédié) -- aucune clé, aucun
paiement construit ici, ce module ne fait qu'appeler et parser la réponse."""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_WALLET_VERIFICATION_URL = "https://x402-cybercentry-wallet-verification.up.railway.app/verify"


async def verify_wallet(address: str) -> dict[str, Any]:
    """Vérifie une adresse de wallet (sanctions/fraude/risque) via Cybercentry (x402,
    0,02 $). Renvoie ``{"available": bool, "raw": dict|None, "error": str|None,
    "amount_usd": float}`` -- jamais une exception qui remonte (même dôme que le
    reste des clients externes)."""
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
    except Exception as exc:  # noqa: BLE001 — corps illisible, jamais une exception qui remonte
        return {
            "available": False, "raw": None, "error": f"réponse illisible ({exc})",
            "amount_usd": result.amount_usd,
        }
    return {"available": True, "raw": raw, "error": None, "amount_usd": result.amount_usd}
