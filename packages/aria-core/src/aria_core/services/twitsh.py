"""Client twit.sh (x402) — recherche/timeline X, COMPLÉMENT à
``aria_core.gateway.x_twitter`` (19/07, #111/#112, décision opérateur tranchée via
AskUserQuestion : jamais un remplacement, jamais la source primaire). Repéré via
x402scan.com (captures opérateur, 667$ volume/24h — le service X du Bazaar le plus
utilisé de tout l'écosystème, 91 520 appels/30j), validé en conditions réelles à 4
reprises (2 paiements de qualité + 2 de vérification de schéma, même segment) :
``x402.twit.sh/tweets/search`` (0,006$/appel, param ``words``) et
``x402.twit.sh/tweets/user`` (0,01$/appel, param ``username`` — confirmé après un
premier essai avec ``from`` en 400 "Missing required query parameter: username").

Schéma X API v2-compatible (``id``/``text``/``created_at``/``author_id``/
``public_metrics``) MAIS ``created_at`` au format Twitter v1.1 legacy
("Sun Jul 19 15:48:00 +0000 2026", pas ISO 8601) — normalisé ici en ISO pour que le
résultat soit un DROP-IN du même shape que
``x_twitter.search_recent_tweets``/``fetch_user_recent_tweets`` ({"text",
"created_at", ...}) : ``conviction_research.py`` n'a besoin d'aucune branche de
parsing séparée pour le repli.

Chaque appel passe par ``x402_executor.fetch_paid_resource`` (plafond hebdo PARTAGÉ
``x402_budget.py``, 5$/semaine, coupe-circuit ``/stop``, wallet CDP dédié) — même dôme
que ``services/ottoai.py``/``services/cybercentry.py``. Aucun plafond dédié
supplémentaire construit ici : le plafond partagé, déjà fail-closed, borne le pire cas
si ce repli est sollicité souvent."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://x402.twit.sh/tweets/search"
_USER_URL = "https://x402.twit.sh/tweets/user"

# Format Twitter v1.1 legacy observé en conditions réelles (19/07) -- distinct de
# l'ISO 8601 renvoyé par l'API X officielle (x_twitter.py).
_LEGACY_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _normalize_created_at(raw: object) -> str | None:
    """Convertit le format Twitter v1.1 legacy en ISO 8601 -- jamais une exception.
    Sans cette normalisation, ``_posting_cadence_from_tweets`` (conviction_research.py)
    échoue silencieusement à parser CHAQUE tweet twit.sh (ValueError capté, ignoré),
    rendant la cadence de publication systématiquement "unknown" malgré des données
    réelles -- bug réel évité en vérifiant le format contre un vrai appel avant de
    coder (norme du 14/07)."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw, _LEGACY_DATE_FORMAT)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return raw


def _parse_tweets(body: bytes) -> list[dict]:
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 -- corps illisible, jamais une exception qui remonte
        return []
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        return []
    tweets: list[dict] = []
    for t in data:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        tweets.append(
            {
                "text": text,
                "created_at": _normalize_created_at(t.get("created_at")),
                "tweet_id": t.get("id"),
                "author_id": t.get("author_id"),
                "public_metrics": t.get("public_metrics") or {},
            }
        )
    return tweets


async def search_tweets(query: str, *, max_results: int = 10) -> list[dict]:
    """Recherche X par requête libre (ticker/adresse/mot-clé), x402 (0,006$/appel).
    Dôme standard : jamais une exception qui remonte, liste vide sur toute panne."""
    q = (query or "").strip()
    if not q:
        return []
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    params = urlencode({"words": q[:500], "maxResults": max(10, min(int(max_results), 100))})
    try:
        result = await x402_executor.fetch_paid_resource(
            f"{_SEARCH_URL}?{params}",
            resource="tweets-search",
            provider="twitsh",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("twitsh: search_tweets échoué (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_tweets(result.body)


async def fetch_user_tweets(username: str, *, max_results: int = 10) -> list[dict]:
    """Timeline récente d'un compte X par son handle, x402 (0,01$/appel). Même dôme
    que ``search_tweets``."""
    handle = (username or "").lstrip("@").strip()
    if not handle:
        return []
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    params = urlencode({"username": handle, "maxResults": max(5, min(int(max_results), 100))})
    try:
        result = await x402_executor.fetch_paid_resource(
            f"{_USER_URL}?{params}",
            resource="tweets-user",
            provider="twitsh",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("twitsh: fetch_user_tweets échoué (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_tweets(result.body)
