"""Client de lecture seule CoinGecko — données fondamentales (Base).

Complète Blockscout (on-chain) et DexScreener (marché court terme) avec des
données fondamentales : market cap, FDV, supply, catégories, âge du token.
Aucune écriture, aucune clé API requise (tier public). Politique d'erreurs
identique à `services/blockscout.py` (cf. AGENTS.md) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- Aucune donnée manquante n'est jamais remplacée par une supposition — le
  champ `error` (et `available=False`) porte l'absence de donnée.
- Échecs consécutifs répétés (>3) : logué, jamais bloquant, jamais de spam Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"

UNAVAILABLE = "donnée fondamentale indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class TokenFundamentals:
    contract: str
    coingecko_id: str | None = None
    name: str | None = None
    symbol: str | None = None
    market_cap_usd: float | None = None
    fully_diluted_valuation_usd: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    max_supply: float | None = None
    categories: list[str] = field(default_factory=list)
    homepage: str | None = None
    whitepaper: str | None = None
    genesis_date: str | None = None
    available: bool = False
    error: str | None = None


class CoinGeckoClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique sans clé)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 2.2) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "coingecko: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "coingecko: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, path: str) -> tuple[object | None, str | None]:
        """GET avec la politique d'erreurs AGENTS.md. Retourne (data, error)."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout CoinGecko)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit CoinGecko)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur CoinGecko)"

            if response.status_code == 404:
                self._record_success()
                return None, "token non listé sur CoinGecko"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_token_fundamentals(self, contract: str, *, platform_id: str = "base") -> TokenFundamentals:
        data, error = await self._get_json(f"/coins/{platform_id}/contract/{contract}")
        if error is not None:
            return TokenFundamentals(contract=contract, available=False, error=error)
        if not isinstance(data, dict):
            return TokenFundamentals(contract=contract, available=False, error=UNAVAILABLE)

        market_data = data.get("market_data") or {}

        def _usd(field_name: str) -> float | None:
            value = (market_data.get(field_name) or {}).get("usd") if isinstance(market_data.get(field_name), dict) else None
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _num(field_name: str) -> float | None:
            value = market_data.get(field_name)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        links = data.get("links") or {}
        homepage_list = links.get("homepage") or []
        whitepaper = links.get("whitepaper")

        return TokenFundamentals(
            contract=contract,
            coingecko_id=data.get("id"),
            name=data.get("name"),
            symbol=data.get("symbol"),
            market_cap_usd=_usd("market_cap"),
            fully_diluted_valuation_usd=_usd("fully_diluted_valuation"),
            circulating_supply=_num("circulating_supply"),
            total_supply=_num("total_supply"),
            max_supply=_num("max_supply"),
            categories=[c for c in (data.get("categories") or []) if c],
            homepage=next((h for h in homepage_list if h), None),
            whitepaper=whitepaper or None,
            genesis_date=data.get("genesis_date"),
            available=True,
            error=None,
        )


coingecko_client = CoinGeckoClient()
