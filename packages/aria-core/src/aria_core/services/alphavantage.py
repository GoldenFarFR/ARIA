"""Client de lecture seule Alpha Vantage — indices actions (proxy ETF), ETF,
matières premières hors métaux précieux (tâche #14 suite, 13/07 -- overlay macro).

Doctrine du dôme, même patron que ``forex.py`` : GET uniquement, aucune écriture,
backoff exponentiel sur 429 (3 tentatives), 1 retry après 5s sur timeout/5xx,
``fetch_*``/``get_*`` ne lèvent jamais sur erreur réseau, ``available=False``
explicite, aucune donnée manquante jamais remplacée par une supposition.

Écart assumé par rapport à Frankfurter (cf. veille
``docs/aria-learning-inbox/2026-07-13-veille-sources-donnees-actions-etf-matieres-
premieres.md``) : clé API requise, plafond gratuit réel de seulement 25
requêtes/jour. Deux conséquences structurelles :

- **Pas d'endpoint indice natif** (``^GSPC``/``^IXIC``) : les "indices actions"
  sont interrogés via leur ETF-réplique (``GLOBAL_QUOTE`` sur SPY/QQQ) --
  ``QuoteResult.is_proxy`` le rend explicite pour tout appelant, jamais présenté
  comme l'indice lui-même.
- **Or/argent non couverts** : aucun endpoint documenté chez ce fournisseur pour
  les métaux précieux (vérifié dans la veille) -- absence structurelle, pas un
  choix de câblage. ``get_commodity`` refuse toute fonction hors de la liste
  blanche ci-dessous, y compris si un appelant tentait "GOLD"/"SILVER".

Cache strict + budget quotidien : un cache en mémoire seul (cf.
``btc_cycles._phase_cache``) perdrait le compte à chaque redémarrage du process
et pourrait dépasser le plafond réel de 25/jour -- ici la persistance est
nécessaire, pas juste une sobriété de bon goût. ``aiosqlite`` + ``aria_db_path()``
(même infra que ``ux_watch.py``/``pump_dump_autopsy.py``), deux tables :
``alphavantage_cache`` (payload JSON, TTL 24h) et ``alphavantage_daily_calls``
(compteur par jour calendaire). Budget interne volontairement plus bas que le
plafond réel (20 au lieu de 25) -- marge de sécurité pour absorber un test
manuel/debug sans jamais toucher le mur.

**Hypothèse assumée, à corriger si l'info exacte est connue** : le jour de
reset du plafond Alpha Vantage n'est pas précisé dans la veille (probablement
minuit heure du marché US, pas UTC). Faute de confirmation, le compteur utilise
un jour calendaire UTC -- déterministe et simple, potentiellement décalé de
quelques heures par rapport au vrai reset du fournisseur. Dans le pire cas ça
sous-utilise légèrement le quota (jamais un dépassement), jamais l'inverse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite
import httpx

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"

UNAVAILABLE = "donnée Alpha Vantage indisponible"

DB_PATH = str(aria_db_path())

# Budget interne < plafond réel (25/jour) -- marge de sécurité volontaire.
DAILY_BUDGET = 20

# TTL du cache : uniforme 24h pour tout (quotes ET matières premières), même si
# la veille signale que les matières premières sont "proches du temps réel"
# chez ce fournisseur -- le plafond de 25/jour domine de toute façon, pas de
# traitement différencié qui compliquerait la logique sans gain réel.
_CACHE_TTL_SECONDS = 24 * 3600

# Symboles ETF-proxy pour les indices actions (pas l'indice natif -- absent de
# l'API). SPY = S&P 500, QQQ = Nasdaq 100.
PROXY_SYMBOLS = {"SPY", "QQQ"}

# Fonctions "commodities" documentées par Alpha Vantage et vérifiées par la
# veille -- liste blanche stricte, aucun paramètre libre. Or/argent absents
# (pas un oubli : aucun endpoint chez ce fournisseur).
COMMODITY_FUNCTIONS = {
    "WTI",
    "BRENT",
    "NATURAL_GAS",
    "COPPER",
    "ALUMINUM",
    "WHEAT",
    "CORN",
    "COTTON",
    "SUGAR",
    "COFFEE",
    "ALL_COMMODITIES",
}


@dataclass
class QuoteResult:
    """Cotation ETF réelle (``GLOBAL_QUOTE``), jamais un point inventé."""

    symbol: str
    price: float | None = None
    change_pct: float | None = None
    latest_trading_day: str | None = None
    is_proxy: bool = False
    stale: bool = False
    available: bool = False
    error: str | None = None


@dataclass
class CommodityResult:
    """Valeur matière première réelle, jamais un point inventé."""

    function: str
    value: float | None = None
    unit: str | None = None
    date: str | None = None
    stale: bool = False
    available: bool = False
    error: str | None = None


def alphavantage_context_enabled() -> bool:
    return os.environ.get("ARIA_ALPHAVANTAGE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS alphavantage_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS alphavantage_daily_calls (
                call_date TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_cached(cache_key: str) -> tuple[dict | None, bool]:
    """Renvoie ``(payload, stale)``. ``payload`` est ``None`` si jamais mis en
    cache ; ``stale=True`` si présent mais expiré (encore utilisable en dernier
    recours si le budget quotidien est épuisé)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT payload, fetched_at FROM alphavantage_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, False
    payload_raw, fetched_at = row
    try:
        payload = json.loads(payload_raw)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, False
    return payload, age >= _CACHE_TTL_SECONDS


async def _set_cached(cache_key: str, payload: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alphavantage_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
            (cache_key, json.dumps(payload), _now()),
        )
        await db.commit()


async def _budget_remaining() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT count FROM alphavantage_daily_calls WHERE call_date = ?", (_today(),)
        )
        row = await cursor.fetchone()
    used = row[0] if row else 0
    return max(0, DAILY_BUDGET - used)


async def _record_call() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alphavantage_daily_calls (call_date, count) VALUES (?, 1) "
            "ON CONFLICT(call_date) DO UPDATE SET count = count + 1",
            (_today(),),
        )
        await db.commit()


class AlphaVantageClient:
    """Client HTTP async, lecture seule, cache + budget quotidien persistants."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url

    def _api_key(self) -> str | None:
        return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip() or None

    async def _get_json(self, params: dict) -> tuple[dict | None, str | None]:
        attempt_429 = 0
        timeout_retried = False
        url = self.base_url

        while True:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.info("alphavantage: timeout (%s)", exc)
                return None, f"{UNAVAILABLE} (timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    logger.info("alphavantage: HTTP 429 apres %s tentatives", attempt_429)
                    return None, f"{UNAVAILABLE} (rate limit)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.info("alphavantage: HTTP %s", response.status_code)
                return None, f"{UNAVAILABLE} (erreur serveur)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.info("alphavantage: %s", exc)
                return None, f"{UNAVAILABLE} ({exc})"

            data = response.json()
            if not isinstance(data, dict) or "Note" in data or "Information" in data:
                # Alpha Vantage renvoie du 200 OK avec un message texte dans
                # "Note"/"Information" quand le plafond est atteint côté serveur
                # (pas un vrai payload) -- traité comme un échec explicite, jamais
                # parsé comme une donnée réelle.
                return None, f"{UNAVAILABLE} (plafond fournisseur atteint)"
            return data, None

    async def _fetch_with_budget(self, cache_key: str, params: dict) -> tuple[dict | None, bool, str | None]:
        """Renvoie ``(payload, stale, error)``. Sert le cache si valide ; sinon
        consulte le budget quotidien avant tout appel réseau réel ; si le budget
        est épuisé, retombe sur le cache même expiré plutôt que rien."""
        await _ensure_tables()

        cached, stale = await _get_cached(cache_key)
        if cached is not None and not stale:
            return cached, False, None

        remaining = await _budget_remaining()
        if remaining <= 0:
            if cached is not None:
                return cached, True, None
            return None, False, f"{UNAVAILABLE} (budget quotidien épuisé, aucune valeur en cache)"

        api_key = self._api_key()
        if not api_key:
            if cached is not None:
                return cached, True, None
            return None, False, f"{UNAVAILABLE} (clé API absente)"

        await _record_call()
        data, error = await self._get_json({**params, "apikey": api_key})
        if error is not None:
            if cached is not None:
                return cached, True, None
            return None, False, error

        await _set_cached(cache_key, data)
        return data, False, None

    async def get_quote(self, symbol: str) -> QuoteResult:
        """Cotation ETF réelle via ``GLOBAL_QUOTE`` -- proxy pour l'indice qu'elle
        réplique (SPY/QQQ), jamais présentée comme l'indice natif."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return QuoteResult(symbol=sym, available=False, error=UNAVAILABLE)

        cache_key = f"GLOBAL_QUOTE:{sym}"
        data, stale, error = await self._fetch_with_budget(
            cache_key, {"function": "GLOBAL_QUOTE", "symbol": sym}
        )
        if error is not None:
            return QuoteResult(symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=error)

        quote = (data or {}).get("Global Quote") if data else None
        if not isinstance(quote, dict) or not quote:
            return QuoteResult(
                symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=UNAVAILABLE
            )

        try:
            price = float(quote.get("05. price"))
        except (TypeError, ValueError):
            price = None
        change_pct_raw = str(quote.get("10. change percent", "")).rstrip("%")
        try:
            change_pct = float(change_pct_raw) if change_pct_raw else None
        except ValueError:
            change_pct = None

        if price is None:
            return QuoteResult(
                symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=UNAVAILABLE
            )

        return QuoteResult(
            symbol=sym,
            price=price,
            change_pct=change_pct,
            latest_trading_day=quote.get("07. latest trading day"),
            is_proxy=sym in PROXY_SYMBOLS,
            stale=stale,
            available=True,
        )

    async def get_commodity(self, function: str) -> CommodityResult:
        """Valeur matière première réelle -- fonction restreinte à la liste
        blanche vérifiée par la veille (hors métaux précieux, absents chez ce
        fournisseur)."""
        fn = (function or "").strip().upper()
        if fn not in COMMODITY_FUNCTIONS:
            return CommodityResult(function=fn, available=False, error=f"{UNAVAILABLE} (fonction non couverte)")

        cache_key = f"COMMODITY:{fn}"
        data, stale, error = await self._fetch_with_budget(cache_key, {"function": fn})
        if error is not None:
            return CommodityResult(function=fn, available=False, error=error)

        series = (data or {}).get("data") if data else None
        if not isinstance(series, list) or not series:
            return CommodityResult(function=fn, available=False, error=UNAVAILABLE)

        latest = series[0]
        try:
            value = float(latest.get("value"))
        except (TypeError, ValueError, AttributeError):
            return CommodityResult(function=fn, available=False, error=UNAVAILABLE)

        return CommodityResult(
            function=fn,
            value=value,
            unit=data.get("unit") if data else None,
            date=latest.get("date") if isinstance(latest, dict) else None,
            stale=stale,
            available=True,
        )


alphavantage_client = AlphaVantageClient()


async def fetch_equities_commodities_context(*, client: AlphaVantageClient | None = None) -> dict | None:
    """Point d'entrée compact pour l'overlay macro des rapports VC (tâche #14
    suite, 13/07). Fail-closed (gate OFF par défaut) ET dégradation douce :
    chaque source (SPY, QQQ, matières premières composite) est indépendante --
    l'absence de l'une n'empêche jamais les autres. ``None`` seulement si les
    TROIS échouent (rien à montrer), jamais une valeur inventée pour combler."""
    if not alphavantage_context_enabled():
        return None

    if client is None:
        client = alphavantage_client

    spy = await client.get_quote("SPY")
    qqq = await client.get_quote("QQQ")
    commodities = await client.get_commodity("ALL_COMMODITIES")

    ctx: dict = {}
    if spy.available:
        ctx["spy"] = {"price": spy.price, "change_pct": spy.change_pct, "date": spy.latest_trading_day, "stale": spy.stale}
    if qqq.available:
        ctx["qqq"] = {"price": qqq.price, "change_pct": qqq.change_pct, "date": qqq.latest_trading_day, "stale": qqq.stale}
    if commodities.available:
        ctx["commodities"] = {"value": commodities.value, "unit": commodities.unit, "date": commodities.date, "stale": commodities.stale}

    return ctx or None
