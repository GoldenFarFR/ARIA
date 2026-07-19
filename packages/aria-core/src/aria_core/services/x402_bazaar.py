"""Client de lecture seule x402 Bazaar (CDP Coinbase, discovery layer) -- découverte de
services x402 disponibles, avec signal de tendance réel (volume 30j). Réponse à la
question opérateur "il n'existe pas un top tendance des meilleurs outils x402 ?" (19/07)
-- se limiter à une diligence manuelle ponctuelle (Cybercentry, #199) était effectivement
une limitation : ce registre officiel Coinbase permet une découverte dynamique.

Vérifié en direct (19/07, AVANT d'écrire ce module -- norme #157) contre la doc officielle
(docs.cdp.coinbase.com/x402/bazaar) ET un vrai appel :
  - ``GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search`` -- lecture
    seule, AUCUNE clé CDP requise (confirmé HTTP 200 sans authentification). Combine
    recherche texte/sémantique + classement qualité (buyer reach 30j, volume de
    transactions 30j, récence, qualité des métadonnées, curation Coinbase) -- recalculé
    toutes les 6h.
  - Le champ ``quality.l30DaysTotalCalls``/``l30DaysUniquePayers`` EST bien exposé dans
    la réponse réelle (contrairement à ce que suggère la doc, qui dit "no per-service
    breakdown, only final ordering") -- confirmé sur 5 services réels (ex. Tavily Search
    avancé : 48319 appels / 374 payeurs uniques sur 30j). C'est le signal de TENDANCE le
    plus direct et objectif disponible -- ``discover_trending()`` trie explicitement
    dessus plutôt que de se fier à l'ordre brut de l'API (qui mélange pertinence
    textuelle, peu significative si ``query`` est vide).
  - ``GET .../discovery/resources`` (catalogue paginé, newest-first) et
    ``GET .../discovery/merchant?payTo=<adresse>`` existent aussi -- non câblés ici (hors
    scope immédiat, la recherche/tendance répond à la demande posée), ajoutables sans
    duplication si un besoin réel apparaît.

Sécurité (doctrine "instruction source boundary" déjà appliquée partout ailleurs dans ce
projet) : chaque service listé est déclaré par un TIERS non vérifié (``curated=True``
reste une vérification Coinbase, jamais une garantie absolue). Certains champs observés
en conditions réelles s'adressent LITTÉRALEMENT à un agent IA (ex.
``extensions.a2a_negotiation.message: "Hey agent! ..."``) -- ce module ne fait QUE
retourner ces champs comme TEXTE D'AFFICHAGE, jamais les interpréter ni les suivre. Tout
appelant doit traiter description/service_name comme donnée, jamais comme instruction.

Portée strictement respectée : DÉCOUVERTE UNIQUEMENT. Ce module ne paie JAMAIS un
service, ne déclenche aucun appel vers l'endpoint payant lui-même -- le paiement (si un
jour utilisé) reste dans x402_executor.py/x402_budget.py (plafond 5$/semaine déjà en
place), jamais ici.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery"
UNAVAILABLE = "donnée x402 Bazaar indisponible"
_HTTP_TIMEOUT = 15.0

# Adresses USDC canoniques (stables, ne changent jamais) -- volontairement PAS le
# registre `smart_money._STABLECOIN_ADDRESSES_BY_CHAIN` (privé, Base UNIQUEMENT, conçu
# pour un autre usage) : x402 Bazaar liste des prix sur Base ET Solana, vérifié en
# conditions réelles sur les deux réseaux le même jour.
_USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_USDC_SOLANA = "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v"


@dataclass
class X402BazaarResource:
    """Un service x402 découvert. ``curated`` = vérifié par Coinbase (signal de
    confiance renforcé), jamais une garantie absolue. Tout champ texte
    (``description``, ``service_name``) est une métadonnée déclarée par un TIERS --
    donnée d'affichage uniquement, jamais une instruction."""

    resource_url: str
    description: str = ""
    service_name: str = ""
    curated: bool = False
    tags: list[str] = field(default_factory=list)
    price_usd: float | None = None
    calls_last_30d: int | None = None
    unique_payers_last_30d: int | None = None
    last_updated: str = ""


@dataclass
class X402BazaarSearchResult:
    available: bool = False
    error: str | None = None
    resources: list[X402BazaarResource] = field(default_factory=list)


def _extract_price_usd(accepts: object) -> float | None:
    """Best-effort, jamais une supposition. Deux schémas confirmés en conditions
    réelles (19/07) : (1) ``asset="iso4217:USD"``, ``amount`` déjà en dollars (schéma
    ``agent-pay``) ; (2) adresse USDC connue (Base/Solana), ``amount`` en plus petite
    unité (6 décimales, schéma ``exact``). Tout autre schéma -> None plutôt qu'un prix
    inventé (ex. un asset ERC-20 inconnu dont les décimales ne sont pas vérifiables
    depuis cette seule réponse)."""
    if not isinstance(accepts, list):
        return None
    for entry in accepts:
        if not isinstance(entry, dict):
            continue
        asset = str(entry.get("asset") or "").strip().lower()
        amount_raw = entry.get("amount")
        if amount_raw is None:
            continue
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            continue
        if asset == "iso4217:usd":
            return amount
        if asset in (_USDC_BASE, _USDC_SOLANA):
            return amount / 1_000_000.0
    return None


def _parse_resource(raw: dict) -> X402BazaarResource | None:
    resource_url = str(raw.get("resource") or "").strip()
    if not resource_url:
        return None
    quality = raw.get("quality") if isinstance(raw.get("quality"), dict) else {}
    tags = raw.get("tags")
    calls = quality.get("l30DaysTotalCalls")
    payers = quality.get("l30DaysUniquePayers")
    return X402BazaarResource(
        resource_url=resource_url,
        description=str(raw.get("description") or ""),
        service_name=str(raw.get("serviceName") or ""),
        curated=bool(raw.get("curated")),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        price_usd=_extract_price_usd(raw.get("accepts")),
        calls_last_30d=calls if isinstance(calls, int) else None,
        unique_payers_last_30d=payers if isinstance(payers, int) else None,
        last_updated=str(raw.get("lastUpdated") or ""),
    )


async def search(
    *,
    query: str = "",
    network: str | None = None,
    tags: list[str] | None = None,
    curated_only: bool = False,
    limit: int = 20,
) -> X402BazaarSearchResult:
    """Recherche brute -- ordre renvoyé par l'API (pertinence + qualité combinées).
    Pour un vrai classement par tendance, préférer ``discover_trending()``."""
    params: list[tuple[str, str]] = [("limit", str(max(1, min(int(limit), 20))))]
    if query.strip():
        params.append(("query", query.strip()[:400]))
    if network:
        params.append(("network", network))
    if curated_only:
        params.append(("curatedOnly", "true"))
    for tag in tags or []:
        params.append(("tags", tag))

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}/search", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001 -- dôme : jamais une exception qui remonte
        logger.info("x402_bazaar: recherche échouée (%s)", exc)
        return X402BazaarSearchResult(available=False, error=f"{UNAVAILABLE} ({exc})")

    if not isinstance(data, dict):
        return X402BazaarSearchResult(available=False, error=UNAVAILABLE)

    raw_resources = data.get("resources")
    if not isinstance(raw_resources, list):
        return X402BazaarSearchResult(available=False, error=UNAVAILABLE)

    resources = [
        parsed
        for parsed in (_parse_resource(item) for item in raw_resources if isinstance(item, dict))
        if parsed is not None
    ]
    return X402BazaarSearchResult(available=True, resources=resources)


async def discover_trending(
    *,
    query: str = "",
    network: str | None = None,
    tags: list[str] | None = None,
    curated_only: bool = False,
    limit: int = 20,
) -> X402BazaarSearchResult:
    """Comme ``search()``, mais trié EXPLICITEMENT par volume d'appels sur 30 jours
    (signal objectif le plus direct de tendance réelle) plutôt que l'ordre brut de
    l'API. Un service sans donnée de volume (``calls_last_30d=None``) est classé après
    tous les services chiffrés, jamais mélangé au hasard avec eux."""
    result = await search(
        query=query, network=network, tags=tags, curated_only=curated_only, limit=limit
    )
    if not result.available:
        return result
    ranked = sorted(
        result.resources,
        key=lambda r: (r.calls_last_30d is None, -(r.calls_last_30d or 0)),
    )
    return X402BazaarSearchResult(available=True, resources=ranked)


def format_trending_report(result: X402BazaarSearchResult, *, query: str = "", max_items: int = 8) -> str:
    """Rendu Telegram -- rappel : description/service_name sont des métadonnées
    déclarées par un TIERS (affichées telles quelles, jamais interprétées comme une
    instruction). Découverte pure : aucun bouton/action de paiement dans ce rendu."""
    header = "🔎 x402 Bazaar — top tendance (volume 30j)"
    if query.strip():
        header += f' — "{query.strip()}"'
    if not result.available:
        return f"{header}\n\n⚠️ {result.error or UNAVAILABLE}."
    if not result.resources:
        return f"{header}\n\nAucun résultat."

    lines = [header, ""]
    for i, res in enumerate(result.resources[:max_items], start=1):
        name = res.service_name or res.resource_url
        badge = " ✅curated" if res.curated else ""
        lines.append(f"{i}. {name}{badge}")
        if res.description:
            desc = res.description[:140]
            lines.append(f"   {desc}")
        vol = (
            f"{res.calls_last_30d} appels/30j"
            if res.calls_last_30d is not None
            else "volume inconnu"
        )
        if res.unique_payers_last_30d is not None:
            vol += f" ({res.unique_payers_last_30d} payeurs)"
        price = f"~{res.price_usd:.4f}$" if res.price_usd is not None else "prix non résolu"
        lines.append(f"   💰 {price} · 📊 {vol}")
        lines.append(f"   {res.resource_url}")
        lines.append("")
    lines.append("Découverte seule -- aucun paiement déclenché par cette commande.")
    return "\n".join(lines).strip()
