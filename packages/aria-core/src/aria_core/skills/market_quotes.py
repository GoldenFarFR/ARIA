"""Cotations connues (crypto majeures + devises) — un chemin STRUCTURÉ, jamais scrappé.

Incident réel (10/07) : une question « prix du BTC/ETH/SOL » était routée vers la
recherche web générique (`web_verify.py`), qui a cité une page périmée comme si elle
était en direct — BTC/SOL rapportés ~30% sous leur vrai prix. Root cause : aucune
distinction entre « une actualité (sport, événement) qui nécessite vraiment une
recherche web » et « le prix d'un actif connu, pour lequel une vraie API de cotation
existe et est bien plus fiable qu'une page web indexée ».

Ce module intercepte les questions de prix sur des actifs RECONNUS (crypto majeures
via CoinGecko, devises majeures via Frankfurter/BCE) et répond depuis ces clients
structurés — jamais une supposition, jamais un texte de page web. Toute question hors
de ce périmètre reconnu (actif inconnu, actualité, sport...) n'est PAS interceptée et
retombe sur le chemin existant (`web_verify.py`), inchangé.

Volontairement PAS de couverture actions/indices (Nasdaq, S&P 500...) : aucune API
gratuite et bien documentée n'a pu être confirmée en direct depuis cet environnement
(candidate évaluée : Stooq — endpoint non officiel, fetch direct bloqué en sandbox,
forme de réponse non confirmable) — cf. doctrine « profondeur proportionnelle à
l'enjeu », pas de client bâti sur une hypothèse. Seam à compléter après vérification
VPS si l'opérateur le souhaite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Symbole/nom (FR+EN, minuscules) -> id CoinGecko. Liste volontairement modeste (les
# actifs les plus demandés en conversation), pas une couverture exhaustive du marché.
_CRYPTO_ALIASES: dict[str, str] = {
    "btc": "bitcoin", "bitcoin": "bitcoin",
    "eth": "ethereum", "ethereum": "ethereum", "ether": "ethereum",
    "sol": "solana", "solana": "solana",
    "doge": "dogecoin", "dogecoin": "dogecoin",
    "xrp": "ripple", "ripple": "ripple",
    "bnb": "binancecoin", "binance coin": "binancecoin",
    "ada": "cardano", "cardano": "cardano",
    "link": "chainlink", "chainlink": "chainlink",
    "matic": "matic-network", "polygon": "matic-network",
    "avax": "avalanche-2", "avalanche": "avalanche-2",
    "dot": "polkadot", "polkadot": "polkadot",
    "ltc": "litecoin", "litecoin": "litecoin",
    "virtual": "virtual-protocol", "virtuals": "virtual-protocol",
}

# Devise (FR+EN, minuscules) -> code ISO. Majeures uniquement (cf. Frankfurter/BCE).
_FOREX_ALIASES: dict[str, str] = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "dollar americain": "USD",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "livre": "GBP", "livre sterling": "GBP", "gbp": "GBP", "pound": "GBP",
    "yen": "JPY", "jpy": "JPY",
    "franc suisse": "CHF", "chf": "CHF",
    "dollar canadien": "CAD", "cad": "CAD",
    "dollar australien": "AUD", "aud": "AUD",
}

_PRICE_QUESTION_RE = re.compile(
    r"\b(prix|cours|vaut|valeur|combien|price|worth|value)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class QuoteMatch:
    kind: str  # "crypto" | "forex"
    ids: list[str]  # coingecko ids OU codes devise détectés (déduplication respectée)


def detect_quote_question(query: str) -> QuoteMatch | None:
    """Détecte une question de prix sur un actif RECONNU. ``None`` si rien de sûr —
    ne devine jamais un actif à partir d'un mot ambigu isolé (fail-closed : mieux vaut
    laisser retomber sur le chemin existant que mal router)."""
    text = (query or "").lower()
    if not _PRICE_QUESTION_RE.search(text):
        return None

    crypto_ids: list[str] = []
    for alias, coin_id in _CRYPTO_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text) and coin_id not in crypto_ids:
            crypto_ids.append(coin_id)
    if crypto_ids:
        return QuoteMatch(kind="crypto", ids=crypto_ids)

    forex_codes: list[str] = []
    for alias, code in _FOREX_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text) and code not in forex_codes:
            forex_codes.append(code)
    if len(forex_codes) >= 2:
        return QuoteMatch(kind="forex", ids=forex_codes)

    return None


async def resolve_known_asset_quote(query: str, *, coingecko_client=None, forex_client=None) -> str | None:
    """Répond depuis une vraie API structurée si la question porte sur un actif
    reconnu, sinon ``None`` (l'appelant retombe sur le chemin web existant).

    Jamais d'exception : une panne de client dégrade en ``None`` (silence), le
    chemin web générique reste le filet de sécurité, inchangé."""
    match = detect_quote_question(query)
    if match is None:
        return None

    try:
        if match.kind == "crypto":
            return await _resolve_crypto(match.ids, coingecko_client)
        return await _resolve_forex(match.ids, forex_client)
    except Exception:  # noqa: BLE001 — dégrade en silence, jamais bloquant
        return None


async def _resolve_crypto(coin_ids: list[str], client=None) -> str | None:
    if client is None:
        from aria_core.services.coingecko import coingecko_client as client
    result = await client.get_simple_price(coin_ids, vs_currencies=["usd"])
    if not result.available or not result.prices:
        return None

    id_to_label = {v: k for k, v in _CRYPTO_ALIASES.items() if len(k) <= 5}
    lines = []
    for coin_id in coin_ids:
        prices = result.prices.get(coin_id)
        if not prices or "usd" not in prices:
            continue
        label = id_to_label.get(coin_id, coin_id).upper()
        lines.append(f"{label} : ${prices['usd']:,.2f}")
    if not lines:
        return None
    return "Cotation en direct (CoinGecko) : " + " · ".join(lines)


async def _resolve_forex(codes: list[str], client=None) -> str | None:
    if client is None:
        from aria_core.services.forex import forex_client as client
    base, *targets = codes
    if not targets:
        return None
    result = await client.get_latest_rates(base, targets)
    if not result.available or not result.rates:
        return None

    lines = [f"1 {base} = {rate:,.4f} {ccy}" for ccy, rate in result.rates.items()]
    date_note = f" (référence BCE {result.date})" if result.date else ""
    return "Taux de change" + date_note + " : " + " · ".join(lines)
