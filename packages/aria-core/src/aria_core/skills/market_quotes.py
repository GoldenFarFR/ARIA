"""Known quotes (major cryptos + currencies) — a STRUCTURED path, never scraped.

Real incident (10/07): a "BTC/ETH/SOL price" question was routed to the
generic web search (`web_verify.py`), which cited a stale page as if it were
live — BTC/SOL reported ~30% below their real price. Root cause: no
distinction between "a news question (sport, event) that genuinely needs a
web search" and "the price of a known asset, for which a real quote API
exists and is far more reliable than an indexed web page".

This module intercepts price questions about RECOGNIZED assets (major
cryptos via CoinGecko, major currencies via Frankfurter/ECB) and answers
from these structured clients — never a guess, never web-page text. Any
question outside this recognized scope (unknown asset, news, sport...) is
NOT intercepted and falls back to the existing path (`web_verify.py`), unchanged.

Deliberately NO coverage of stocks/indices (Nasdaq, S&P 500...): no free,
well-documented API could be confirmed live from this environment (candidate
evaluated: Stooq — unofficial endpoint, direct fetch blocked in the sandbox,
response shape unconfirmable) — see the "depth proportional to the stakes"
doctrine, no client built on a guess. A seam to fill in after a VPS
verification if the operator wants it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Symbol/name (FR+EN, lowercase) -> CoinGecko id. Deliberately modest list (the
# assets most requested in conversation), not exhaustive market coverage.
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

# Currency (FR+EN, lowercase) -> ISO code. Majors only (see Frankfurter/ECB).
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
    """Detects a price question about a RECOGNIZED asset. ``None`` if nothing is
    certain — never guesses an asset from an isolated ambiguous word
    (fail-closed: better to fall back to the existing path than to misroute)."""
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
    """Answers from a real structured API if the question is about a
    recognized asset, otherwise ``None`` (the caller falls back to the
    existing web path).

    Never an exception: a client failure degrades to ``None`` (silent), the
    generic web path stays the unchanged safety net."""
    match = detect_quote_question(query)
    if match is None:
        return None

    try:
        if match.kind == "crypto":
            return await _resolve_crypto(match.ids, coingecko_client)
        return await _resolve_forex(match.ids, forex_client)
    except Exception:  # noqa: BLE001 — degrades silently, never blocking
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
