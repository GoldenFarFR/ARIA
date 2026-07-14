"""Client GeckoTerminal (lecture seule, public, sans clé) -- côté aria-core (#157).

Un client GeckoTerminal existe déjà côté ``vanguard/backend`` (chart data pour le
produit), mais aria-core (Telegram/CLI, tourne aussi standalone sans le backend
FastAPI) n'a AUCUNE dépendance vers ``vanguard/backend`` et ne doit pas en créer
une -- inverserait le sens de dépendance du monorepo. Ce module est donc un
client séparé, léger, avec ses propres dataclasses (pas les modèles Pydantic du
backend), pensé uniquement pour les besoins de l'évaluateur wallet (#157) :
- ``get_pool_created_at`` : horodatage de création d'un pool (entrée précoce).
- ``resolve_primary_pool`` : résout le pool réel (plus forte liquidité) d'un token.
- ``get_ohlcv`` : historique de prix pour valoriser un trade (PnL FIFO) -- délègue
  à ``services/ohlcv.py`` (correction 14/07, cf. docstring de la méthode) plutôt
  que de dupliquer un second client OHLCV avec une fenêtre plus étroite.

Réseau : Base par défaut (doctrine ARIA : Base uniquement pour tout SAUF le
wallet-scoring #157, 14/07 -- seule capacité multi-chaînes EVM à ce jour, cf.
``services/blockscout.py`` pour le même registre de chaînes). Aucune donnée
manquante n'est jamais remplacée par une supposition -- ``available=False``/
``error`` portent l'absence de donnée, même politique que ``blockscout.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée GeckoTerminal indisponible"

BASE_URL = "https://api.geckoterminal.com/api/v2"
NETWORK = "base"

# Correspondance chaîne ARIA (même vocabulaire que blockscout.CHAIN_IDS) ->
# identifiant réseau GeckoTerminal (#157, wallet-scoring multi-chaînes, 14/07).
GECKO_NETWORK_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "eth",
    "bnb": "bsc",
}

# Palier gratuit GeckoTerminal ~30 req/min -- même throttle que le client existant
# côté vanguard (2.1s), valeur déjà éprouvée en production.
_MIN_INTERVAL = 2.1


@dataclass
class PoolMetadata:
    pool_address: str
    created_at: datetime | None = None
    available: bool = True
    error: str | None = None


@dataclass
class OHLCVResult:
    candles: list[Candle] = field(default_factory=list)
    available: bool = True
    error: str | None = None


class GeckoTerminalClient:
    """Client HTTP async, lecture seule, throttle conservateur (API publique gratuite)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = _MIN_INTERVAL) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET avec retry sur 429/5xx/timeout -- même politique que blockscout.py
        (#157, correction 14/07 : cette fonction ne retentait jamais un rate limit,
        marquant silencieusement "indisponible" au premier 429 rencontré, sans log
        -- diagnostic impossible. Un wallet actif (~20 tokens x 2 appels) peut
        facilement déclencher un 429 isolé sur le palier gratuit ; le retenter une
        fois suffit dans l'immense majorité des cas plutôt que d'abandonner net."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params, headers={"Accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.warning("geckoterminal: timeout sur %s -> %s", url, exc)
                return None, f"{UNAVAILABLE} (timeout GeckoTerminal)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    logger.warning("geckoterminal: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                    return None, f"{UNAVAILABLE} (rate limit GeckoTerminal)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.warning("geckoterminal: HTTP %s sur %s", response.status_code, url)
                return None, f"{UNAVAILABLE} (erreur serveur GeckoTerminal)"

            if response.status_code in (400, 404):
                return None, f"{UNAVAILABLE} (HTTP {response.status_code})"
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning("geckoterminal: %s", exc)
                return None, f"{UNAVAILABLE} ({exc})"

            return response.json(), None

    async def get_pool_created_at(self, pool_address: str, *, network: str = NETWORK) -> PoolMetadata:
        data, error = await self._get_json(f"/networks/{network}/pools/{pool_address}")
        if error is not None:
            return PoolMetadata(pool_address=pool_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolMetadata(pool_address=pool_address, available=False, error=UNAVAILABLE)

        attrs = (data.get("data") or {}).get("attributes") or {}
        raw = attrs.get("pool_created_at")
        created_at = None
        if raw:
            try:
                created_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        if created_at is None:
            return PoolMetadata(pool_address=pool_address, available=False, error="date de création du pool indisponible")
        return PoolMetadata(pool_address=pool_address, created_at=created_at, available=True, error=None)

    async def resolve_primary_pool(self, token_address: str, *, network: str = NETWORK) -> PoolMetadata:
        """Résout le pool PRINCIPAL d'un token (celui à la plus forte liquidité,
        `reserve_in_usd`) -- #157 : `get_pool_created_at`/`get_ohlcv` attendent une
        adresse de POOL, pas un contrat de TOKEN (deux choses différentes en AMM).
        Correction d'un bug latent : le code appelant passait directement l'adresse
        du contrat token là où une adresse de pool était attendue. Sert aussi de
        base à l'exclusion multi-token du wash-trading (#157, correction 14/07) --
        le pool RÉEL de chaque token, pas une adresse statique unique. ``network``
        (#157 multi-chaînes, 14/07) : identifiant réseau GeckoTerminal (cf.
        ``GECKO_NETWORK_SLUGS``), ``"base"`` par défaut -- comportement historique
        inchangé pour tout appelant existant."""
        data, error = await self._get_json(f"/networks/{network}/tokens/{token_address}/pools")
        if error is not None:
            return PoolMetadata(pool_address=token_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolMetadata(pool_address=token_address, available=False, error=UNAVAILABLE)

        pools = data.get("data") or []
        best_attrs: dict | None = None
        best_liquidity = -1.0
        for item in pools:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") or {}
            try:
                liquidity = float(attrs.get("reserve_in_usd") or 0.0)
            except (TypeError, ValueError):
                liquidity = 0.0
            if liquidity > best_liquidity:
                best_liquidity = liquidity
                best_attrs = attrs

        if not best_attrs or not best_attrs.get("address"):
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

        pool_address = str(best_attrs["address"])
        raw_created = best_attrs.get("pool_created_at")
        created_at = None
        if raw_created:
            try:
                created_at = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        return PoolMetadata(pool_address=pool_address, created_at=created_at, available=True, error=None)

    async def get_ohlcv(self, pool_address: str, *, network: str = NETWORK, **_kwargs: object) -> OHLCVResult:
        """Délègue à ``services.ohlcv.ohlcv_client`` -- correction 14/07 (#157) :
        cette méthode réimplémentait un second client GeckoTerminal avec sa
        propre fenêtre fixe (200 bougies 1h ~ 8 jours), alors qu'un client
        GeckoTerminal existait déjà (``services/ohlcv.py``, échelle jour(120)
        → 4h(180) → 1h(240), déjà éprouvée en prod par `vc_predictions`/
        `weekly_training`/`pump_dump_autopsy`) -- violation de la doctrine
        "jamais dupliquer un client existant", et cause RÉELLE (confirmée par
        un re-test opérateur après le fix retry/429 du même jour, résultat
        identique) des jambes "sans prix" sur un wallet dont l'historique de
        trades dépasse 8 jours : la fenêtre 1h ne remontait simplement pas
        assez loin, ce n'était pas un problème de rate-limit. ``network``
        (#157 multi-chaînes, 14/07) transite jusqu'à ``services/ohlcv.py`` (qui
        acceptait déjà ce paramètre, jamais utilisé jusqu'ici). ``**_kwargs``
        absorbe d'éventuels period/aggregate/limit hérités (aucun appelant en
        prod n'en passe actuellement) sans lever."""
        from aria_core.services.ohlcv import ohlcv_client as _wide_ohlcv_client

        wide = await _wide_ohlcv_client.get_ohlcv(pool_address, network=network)
        if not wide.available or not wide.candles:
            return OHLCVResult(candles=[], available=False, error=wide.error or UNAVAILABLE)
        return OHLCVResult(candles=wide.candles, available=True, error=None)


def price_at(ohlcv: OHLCVResult, ts: int) -> float | None:
    """Prix (clôture de la bougie la plus proche à ou avant ``ts``) -- jamais une
    interpolation ou une supposition : ``None`` si aucune bougie ne précède ``ts``."""
    candidates = [c for c in ohlcv.candles if c.ts <= ts]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.ts).close


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


geckoterminal_client = GeckoTerminalClient()
