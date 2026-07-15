"""Client GeckoTerminal (lecture seule, public, sans clé) -- côté aria-core (#157).

Un client GeckoTerminal existe déjà côté ``vanguard/backend`` (chart data pour le
produit), mais aria-core (Telegram/CLI, tourne aussi standalone sans le backend
FastAPI) n'a AUCUNE dépendance vers ``vanguard/backend`` et ne doit pas en créer
une -- inverserait le sens de dépendance du monorepo. Ce module est donc un
client séparé, léger, avec ses propres dataclasses (pas les modèles Pydantic du
backend), pensé uniquement pour les besoins de l'évaluateur wallet (#157) :
- ``get_pool_created_at`` : horodatage de création d'un pool (entrée précoce).
- ``resolve_primary_pool`` : résout le pool réel d'un token (volume 24h plausible,
  réserve en départage -- cf. sa docstring pour le correctif du 14/07).
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
# "bnb" retiré (14/07) -- Blockscout ne sert pas BNB Smart Chain (cf.
# blockscout.CHAIN_IDS), inutile de garder son slug GeckoTerminal seul.
# Étendu (14/07) aux 11 chaînes restantes du classement TVL dynamique (#157,
# services/defillama.py) -- slugs VÉRIFIÉS EN DIRECT (GET
# https://api.geckoterminal.com/api/v2/networks), pas supposés : le
# vocabulaire GeckoTerminal ne suit pas toujours le nom usuel de la chaîne
# ("gnosis" -> "xdai", "zksync era" -> "zksync" et non "zksync_era").
GECKO_NETWORK_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "eth",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "polygon": "polygon_pos",
    "celo": "celo",
    "gnosis": "xdai",
    "scroll": "scroll",
    "zksync": "zksync",
    "rootstock": "rootstock",
    "unichain": "unichain",
    "soneium": "soneium",
    "mode": "mode",
}

# Palier gratuit GeckoTerminal ~30 req/min -- même throttle que le client existant
# côté vanguard (2.1s), valeur déjà éprouvée en production.
_MIN_INTERVAL = 2.1

# Seuil de plausibilité réserve/volume pour `resolve_primary_pool` (correctif
# 14/07, cf. sa docstring) -- calibré sur données réelles (requête directe
# GeckoTerminal, token WETH sur Base, 20 pools) : les pools légitimes de la
# liste avaient un ratio réserve/volume dans ~[0.01, 5] (ex. WETH/USDC 0.3%
# réel ~1.4x), tandis que le pool corrompu écarté par ce correctif affichait
# un ratio ~204 000x -- marge de plusieurs ordres de grandeur, seuil choisi
# largement en dessous pour rester robuste sans risquer d'exclure un pool
# légitime à la marge.
_PLAUSIBILITY_RATIO_MAX = 1000.0


def _pool_is_plausible(reserve_usd: float, volume_h24_usd: float) -> bool:
    """Un pool est jugé implausible si sa réserve déclarée et son volume 24h
    divergent dans des proportions statistiquement incohérentes pour un pool
    réel -- dans UN sens (réserve énorme, volume quasi nul : signal de
    `reserve_in_usd` corrompu/spoofé, cas réel confirmé 14/07) OU DANS L'AUTRE
    (volume énorme, réserve quasi nulle : signal classique de wash-trading).
    Une réserve nulle/négative est toujours implausible (aucune liquidité
    réelle ne peut avoir généré un swap). Un volume nul n'est PAS en soi
    disqualifiant (un token légitime peut simplement n'avoir eu aucun trade
    dans les dernières 24h) -- seul le RATIO extrême, quand il est calculable,
    disqualifie."""
    if reserve_usd <= 0:
        return False
    if volume_h24_usd <= 0:
        return True
    ratio = max(reserve_usd / volume_h24_usd, volume_h24_usd / reserve_usd)
    return ratio <= _PLAUSIBILITY_RATIO_MAX


@dataclass
class PoolMetadata:
    pool_address: str
    created_at: datetime | None = None
    reserve_usd: float | None = None  # 15/07 (défense anti-dust/scam-pool, #157) -- ``None``
    # = inconnu (jamais construit par un appelant qui ne le fournit pas, ex. tests
    # existants) et traité comme "faire confiance" (fail-open), PAS comme "liquidité
    # nulle" -- seule une valeur CONFIRMÉE sous le plancher doit bloquer la
    # valorisation OHLCV (cf. WEIGHTS.min_pool_liquidity_usd_for_pricing).
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
        """Résout le pool PRINCIPAL d'un token -- #157 : `get_pool_created_at`/
        `get_ohlcv` attendent une adresse de POOL, pas un contrat de TOKEN (deux
        choses différentes en AMM). Correction d'un bug latent : le code appelant
        passait directement l'adresse du contrat token là où une adresse de pool
        était attendue. Sert aussi de base à l'exclusion multi-token du
        wash-trading (#157, correction 14/07) -- le pool RÉEL de chaque token,
        pas une adresse statique unique. ``network`` (#157 multi-chaînes, 14/07) :
        identifiant réseau GeckoTerminal (cf. ``GECKO_NETWORK_SLUGS``), ``"base"``
        par défaut -- comportement historique inchangé pour tout appelant existant.

        **Correctif sélection de pool (relecture 14/07, suite #157)** : le critère
        historique ("plus fort `reserve_in_usd`") a produit un cas réel confirmé où
        un pool WETH annonçant 7,6 MILLIARDS de dollars de réserve pour 37 000
        dollars de volume 24h (ratio ~204 000x, `reserve_in_usd` visiblement
        corrompu/spoofé côté GeckoTerminal pour ce pool exotique) a été choisi à la
        place du vrai pool WETH/USDC utilisé dans une transaction réelle -- un
        écart de prix de ~8x, jamais signalé comme erreur (`available=True`), donc
        pire qu'une jambe simplement non-priced. Nouveau critère (cf.
        `_pool_is_plausible`) : filtre d'abord les pools dont le ratio
        réserve/volume est statistiquement implausible dans un sens ou l'autre
        (réserve gonflée sans volume réel = signal de donnée corrompue ; volume
        gonflé sans réserve réelle = signal de wash-trading), PUIS trie les
        survivants par volume 24h (reflète l'usage réel, plus dur à falsifier
        durablement qu'une réserve déclarée), `reserve_in_usd` servant de
        départage secondaire. Un token à POOL UNIQUE (immense majorité des cas
        hors wallet-scoring) n'est JAMAIS soumis au filtre -- ce pool est
        toujours retenu, comportement strictement inchangé pour ce cas. Un
        token à plusieurs pools dont AUCUN ne passe le filtre échoue
        honnêtement (`available=False`) plutôt que de retomber sur le pire des
        choix disponibles."""
        data, error = await self._get_json(f"/networks/{network}/tokens/{token_address}/pools")
        if error is not None:
            return PoolMetadata(pool_address=token_address, available=False, error=error)
        if not isinstance(data, dict):
            return PoolMetadata(pool_address=token_address, available=False, error=UNAVAILABLE)

        pools = data.get("data") or []
        candidates: list[tuple[dict, float, float]] = []
        for item in pools:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") or {}
            try:
                reserve = float(attrs.get("reserve_in_usd") or 0.0)
            except (TypeError, ValueError):
                reserve = 0.0
            volume_raw = (attrs.get("volume_usd") or {}).get("h24") if isinstance(attrs.get("volume_usd"), dict) else None
            try:
                volume = float(volume_raw or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            candidates.append((attrs, reserve, volume))

        if not candidates:
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

        if len(candidates) == 1:
            # Pool unique -- jamais soumis au filtre de plausibilité (rien à
            # départager), comportement strictement inchangé.
            best_attrs, best_reserve, _volume = candidates[0]
        else:
            plausible = [c for c in candidates if _pool_is_plausible(c[1], c[2])]
            if not plausible:
                return PoolMetadata(
                    pool_address=token_address,
                    available=False,
                    error="aucun pool plausible pour ce token (réserve/volume incohérents sur tous les pools trouvés)",
                )
            best_attrs, best_reserve, _best_volume = max(plausible, key=lambda c: (c[2], c[1]))

        if not best_attrs.get("address"):
            return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

        pool_address = str(best_attrs["address"])
        raw_created = best_attrs.get("pool_created_at")
        created_at = None
        if raw_created:
            try:
                created_at = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
            except ValueError:
                created_at = None

        return PoolMetadata(
            pool_address=pool_address, created_at=created_at, reserve_usd=best_reserve, available=True, error=None,
        )

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
