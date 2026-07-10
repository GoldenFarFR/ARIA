"""Client de lecture seule Frankfurter (taux de change) — devises majeures.

Frankfurter (`frankfurter.dev`) republie les taux de référence quotidiens de la
Banque Centrale Européenne (BCE) : gratuit, sans clé, sans limite de requêtes
documentée, API stable et documentée depuis des années (contrairement à Clanker
plus tôt aujourd'hui — même doctrine « profondeur proportionnelle à l'enjeu » :
seuls des clients bien documentés/vérifiables sont câblés sans détour). Taux de
référence BCE = quotidiens (jours ouvrés), pas tick-par-tick — largement suffisant
pour une question conversationnelle « combien vaut le dollar en euro », très
supérieur à une page web scrappée sans preuve de fraîcheur (cf. incident 10/07 :
prix BTC/SOL cités depuis une page périmée comme si elle était « en direct »).

Forme de requête/réponse (``/latest?base=X&symbols=Y`` → ``{"amount":1,"base":"USD",
"date":"...","rates":{"EUR":...}}``) confirmée par recherche croisée (documentation
tierce indépendante, cohérente entre plusieurs sources) — le fetch DIRECT depuis cet
environnement cloud a été bloqué en HTTP 403 (même comportement anti-bot que
Clanker/Virtuals plus tôt aujourd'hui), donc **pas testée en direct** ; à reconfirmer
depuis le VPS avant un premier appel réel, même prudence que les autres clients de
ce dossier.

Mêmes politiques que les autres clients de ce dossier :
- Aucune écriture, GET uniquement.
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer.
- Timeout / 5xx : 1 retry après 5s, puis fallback explicite.
- ``fetch_*`` ne lève JAMAIS sur erreur réseau : renvoient un résultat ``available=False``.
- Aucune donnée manquante n'est jamais remplacée par une supposition.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.frankfurter.dev/v1"

UNAVAILABLE = "donnée de change indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class ExchangeRateResult:
    """Taux de référence BCE réel pour une paire, jamais un point inventé."""

    base: str
    rates: dict[str, float] = field(default_factory=dict)
    date: str | None = None  # date de référence BCE (YYYY-MM-DD), fournie par l'API
    available: bool = False
    error: str | None = None


class ForexClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique sans clé)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 0.5) -> None:
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
                "forex: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "forex: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers={"accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Frankfurter)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Frankfurter)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Frankfurter)"

            if response.status_code in (400, 404):
                self._record_success()
                return None, f"{UNAVAILABLE} (devise inconnue)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_latest_rates(self, base: str, symbols: list[str]) -> ExchangeRateResult:
        """Taux de référence BCE les plus récents pour ``base`` -> chaque devise de
        ``symbols`` (ex. ``base="USD"``, ``symbols=["EUR"]``)."""
        base_ccy = (base or "").strip().upper()
        syms = [s.strip().upper() for s in symbols if s and s.strip()]
        if not base_ccy or not syms:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        url = f"{self.base_url}/latest?base={base_ccy}&symbols={','.join(syms)}"
        data, error = await self._get_json(url)
        if error is not None:
            return ExchangeRateResult(base=base_ccy, available=False, error=error)
        if not isinstance(data, dict):
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        raw_rates = data.get("rates")
        if not isinstance(raw_rates, dict) or not raw_rates:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        rates: dict[str, float] = {}
        for ccy, value in raw_rates.items():
            try:
                rates[str(ccy).upper()] = float(value)
            except (TypeError, ValueError):
                continue
        if not rates:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        return ExchangeRateResult(
            base=base_ccy, rates=rates, date=data.get("date"), available=True, error=None
        )


forex_client = ForexClient()
