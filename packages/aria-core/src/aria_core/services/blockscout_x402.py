"""Client Blockscout Pro (x402) — holders enrichis d'un token, COMPLÉMENT au client
gratuit (``services/blockscout.py::get_token_holders``, palier Free 5 req/s, jamais
remplacé). Réponse à la demande opérateur (21/07) de bâtir une extraction en masse
pour construire une intelligence wallet/entité en interne (même famille d'objectif
que Nansen/Arkham, déjà diligenciés, jamais construits faute de budget).

Endpoint vérifié en conditions réelles (21/07, 2 paiements réussis, données réelles
reçues) : ``https://api.blockscout.com/{chain_id}/api/v2/tokens/{contract}/holders``,
0,002$/appel. Réponse BIEN plus riche que le palier gratuit standard : labels
d'entité (ex. "Moonwell", "Morpho Markets Router", "UniswapV3Pool"), statut
``is_verified``/``is_scam``, score ``reputation`` -- exactement le type de signal
qui manque à ``smart_money.py`` (exclusion holders par heuristique brute
``is_contract`` seule aujourd'hui, jamais un vrai label d'entité).

Timeout critique (21/07, bug réel trouvé et corrigé dans x402_executor.py) : le
règlement d'un paiement sur cet endpoint prend RÉELLEMENT ~28-45s (vérifié sur
plusieurs appels réels), très au-dessus du défaut 12s de ``fetch_paid_resource``
-- ``_HOLDERS_TIMEOUT_S`` ci-dessous choisi avec marge généreuse au-dessus du pire
cas observé, jamais le défaut implicite.

Chaque appel passe par ``x402_executor.fetch_paid_resource`` (plafond hebdo PARTAGÉ
``x402_budget.py``, 5$/semaine, coupe-circuit ``/stop``, wallet CDP dédié) -- même
dôme que ``services/twitsh.py``/``services/cybercentry.py``. Aucun plafond dédié
supplémentaire ici : le plafond partagé, déjà fail-closed, borne le pire cas."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_HOLDERS_URL = "https://api.blockscout.com/{chain_id}/api/v2/tokens/{contract}/holders"

# Vérifié en direct (21/07) : Base uniquement pour l'instant -- seule chaîne
# confirmée contre un vrai paiement réussi. Ajouter une entrée ici seulement après
# vérification empirique, jamais deviné (norme du 14/07).
_CHAIN_IDS: dict[str, str] = {"base": "8453"}

# Règlement du paiement observé entre ~28s et ~45s sur plusieurs appels réels --
# marge généreuse au-dessus du pire cas mesuré, jamais le défaut 12s de
# fetch_paid_resource (trop court, cf. docstring module).
_HOLDERS_TIMEOUT_S = 75.0


def _parse_holders(body: bytes) -> list[dict]:
    """Parse défensif -- liste vide sur tout corps illisible/inattendu, jamais une
    exception qui remonte (même dôme que le reste des clients externes)."""
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    holders: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        tags = []
        metadata = address.get("metadata") if isinstance(address.get("metadata"), dict) else {}
        for tag in (metadata.get("tags") or []):
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(str(tag["name"]))
        holders.append({
            "holder_address": address.get("hash") or "",
            "holder_name": address.get("name"),
            "is_contract": bool(address.get("is_contract")),
            "is_verified": bool(address.get("is_verified")),
            "is_scam": bool(address.get("is_scam")),
            "reputation": address.get("reputation"),
            "tags": tags,
            "value": item.get("value"),
        })
    return holders


async def get_token_holders_x402(
    contract: str, *, chain: str = "base", token_symbol: str = "",
) -> list[dict]:
    """Holders enrichis d'un token via Blockscout Pro (x402, 0,002$/appel). Dôme
    standard : jamais une exception qui remonte, liste vide sur toute panne
    (budget épuisé, /stop actif, solde insuffisant, timeout, réponse illisible).
    ``token_symbol`` (traçabilité, même patron que #143) : transmis tel quel
    jusqu'au journal x402_budget."""
    addr = (contract or "").strip()
    chain_id = _CHAIN_IDS.get(chain)
    if not addr or not chain_id:
        return []

    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    url = _HOLDERS_URL.format(chain_id=chain_id, contract=addr)
    try:
        result = await x402_executor.fetch_paid_resource(
            url,
            resource="token-holders",
            provider="blockscout",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
            contract=addr,
            token_symbol=token_symbol,
            timeout=_HOLDERS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("blockscout_x402: get_token_holders_x402 échoué (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_holders(result.body)
