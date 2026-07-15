"""Client de lecture seule GeckoTerminal — séries OHLCV (bougies) pour Base.

Fournit la **matière première** de l'analyse technique : une série de bougies
OHLCV réelles pour un pool DEX, que `skills/ta_levels.py` transforme en niveaux
(supports / résistances / tendance) et que `skills/chart_render.py` trace.

Tier public GeckoTerminal (aucune clé requise). Politique d'erreurs identique à
`services/coingecko.py` (cf. AGENTS.md) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- 400 / 404 (pool inconnu, réseau non couvert) : `available=False` + message clair.
- Aucune donnée manquante n'est jamais remplacée par une supposition — l'absence
  est portée par `available=False` et `error`, jamais par une bougie inventée.

Le module ne dépend que de `ta_levels.Candle` (dataclass pure, sans I/O) pour
partager la MÊME structure de bougie de bout en bout (aucune duplication).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

BASE_URL = "https://api.geckoterminal.com/api/v2"

UNAVAILABLE = "série OHLCV indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

# Réseau GeckoTerminal pour la chaîne Base (seule chaîne au lancement).
DEFAULT_NETWORK = "base"

# Ordre de repli : on veut d'abord un cadre journalier (niveaux macro), et si le
# token est trop jeune pour avoir assez de bougies day, on retombe sur du 4h puis
# du 1h — de sorte qu'un token récent ait quand même des niveaux exploitables.
# (period GeckoTerminal, aggregate, limit, libellé de timeframe rapporté).
_FETCH_LADDER: tuple[tuple[str, int, int, str], ...] = (
    ("day", 1, 120, "1D"),
    ("hour", 4, 180, "4H"),
    ("hour", 1, 240, "1H"),
)

# En dessous de ce nombre de bougies, une fenêtre est jugée trop maigre pour des
# niveaux fiables → on tente le timeframe plus fin suivant dans l'échelle.
_MIN_USEFUL_CANDLES = 20


@dataclass
class OHLCVResult:
    """Série OHLCV d'un pool, ou l'absence explicite de donnée.

    ``candles`` est trié par horodatage croissant. ``timeframe`` indique quel
    cran de l'échelle a réellement fourni la donnée (1D / 4H / 1H).
    """

    pool_address: str
    network: str = DEFAULT_NETWORK
    candles: list[Candle] = field(default_factory=list)
    timeframe: str | None = None
    available: bool = False
    error: str | None = None


def _parse_candles(payload: object) -> list[Candle]:
    """Extrait ``data.attributes.ohlcv_list`` en ``list[Candle]`` triée.

    Chaque ligne GeckoTerminal = ``[ts, open, high, low, close, volume]``. Une
    ligne malformée est ignorée (jamais d'exception qui remonte), fidèle au dôme :
    on ne fabrique pas de valeur, on écarte ce qui n'est pas exploitable.
    """
    if not isinstance(payload, dict):
        return []
    rows = (
        payload.get("data", {})
        .get("attributes", {})
        .get("ohlcv_list", [])
    )
    if not isinstance(rows, list):
        return []
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            candles.append(
                Candle(
                    ts=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (TypeError, ValueError):
            continue
    candles.sort(key=lambda c: c.ts)
    return candles


class OHLCVClient:
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
                "ohlcv: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "ohlcv: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, path: str, params: dict[str, object]) -> tuple[object | None, str | None]:
        """GET avec la politique d'erreurs AGENTS.md. Retourne (data, error)."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    response = await client.get(
                        url, params=params, headers={"Accept": "application/json"}
                    )
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout GeckoTerminal)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 apres {attempt_429} tentatives")
                    return None, f"{UNAVAILABLE} (rate limit GeckoTerminal)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur GeckoTerminal)"

            if response.status_code in (400, 404):
                self._record_success()
                return None, "pool introuvable sur GeckoTerminal"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_ohlcv(
        self, pool_address: str, *, network: str = DEFAULT_NETWORK, min_useful_candles: int = _MIN_USEFUL_CANDLES,
    ) -> OHLCVResult:
        """Récupère la meilleure série OHLCV disponible pour un pool.

        Parcourt l'échelle 1D → 4H → 1H et s'arrête au premier timeframe qui
        fournit assez de bougies (`min_useful_candles`, défaut
        `_MIN_USEFUL_CANDLES`). Si aucun n'atteint le seuil, renvoie la plus
        fournie obtenue ; si rien n'est obtenu, un `OHLCVResult(available=False)`
        explicite.

        ``min_useful_candles`` (#182, 15/07, correctif de vitesse wallet-scoring) :
        le seuil par défaut (20 bougies) a du sens pour `ta_levels`/`chart_render`
        (besoin d'assez de bougies pour calculer support/résistance), mais AUCUN
        sens pour un appelant qui n'utilise que `price_at` (une seule bougie la
        plus proche d'un timestamp) -- ce cas se contente d'UNE bougie et n'a
        jamais besoin d'escalader jusqu'à 2 appels GeckoTerminal supplémentaires
        (jour insuffisant -> 4h -> 1h) pour un token jeune/microcap qui n'a pas
        encore 20 bougies journalières. Défaut inchangé (`_MIN_USEFUL_CANDLES`)
        pour tous les appelants existants -- aucune régression sur `/vc`."""
        pool = (pool_address or "").strip()
        if not pool:
            return OHLCVResult(pool_address="", network=network, error=f"{UNAVAILABLE} (pool absent)")

        best: OHLCVResult | None = None
        last_error: str | None = None

        for period, aggregate, limit, label in _FETCH_LADDER:
            data, error = await self._get_json(
                f"/networks/{network}/pools/{pool}/ohlcv/{period}",
                {"aggregate": aggregate, "limit": limit},
            )
            if error is not None:
                last_error = error
                continue
            candles = _parse_candles(data)
            if not candles:
                last_error = f"{UNAVAILABLE} (aucune bougie {label})"
                continue
            result = OHLCVResult(
                pool_address=pool,
                network=network,
                candles=candles,
                timeframe=label,
                available=True,
                error=None,
            )
            if len(candles) >= min_useful_candles:
                return result
            # Fenêtre maigre : on la garde en repli mais on tente plus fin.
            if best is None or len(candles) > len(best.candles):
                best = result

        if best is not None:
            return best
        return OHLCVResult(
            pool_address=pool, network=network, error=last_error or UNAVAILABLE
        )


ohlcv_client = OHLCVClient()
