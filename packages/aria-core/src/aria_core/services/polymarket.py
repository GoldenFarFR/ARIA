"""Client de lecture seule Polymarket (Gamma API) — signal macro par marché de
prédiction (#59).

Expose la probabilité IMPLICITE (prix du marché, 0-1) d'événements macro réels
(ex. décisions de taux Fed) — un signal complémentaire à `btc_cycles` (qui lit
le cycle de halving, pas les anticipations de politique monétaire). Aucune
écriture, aucune clé API requise (Gamma API publique). Politique d'erreurs
identique à `services/coingecko.py` (cf. AGENTS.md) :
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- Aucune donnée manquante n'est jamais remplacée par une supposition — le
  champ `error` (et `available=False`) porte l'absence de donnée.

Câblé dans le contexte LLM de `/vc` (`vc_analysis._fetch_polymarket_signals`)
depuis le 10/07 -- note "seam dormant" ci-dessus périmée, corrigée le 19/07
(dérive doc/code trouvée en auditant l'unification /vc<->momentum). Depuis le
19/07, `momentum_entry.py` réutilise le MÊME client + le MÊME formatteur
(`format_polymarket_prompt_lines`) pour la même profondeur d'analyse.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://gamma-api.polymarket.com"
UNAVAILABLE = "signal Polymarket indisponible"

# Tags Polymarket à interroger pour le contexte macro (#59). Uniquement
# ``fed-rates`` pour l'instant : testé en direct le 10/07, donne le marché de
# prédiction le plus liquide sur les décisions de taux Fed -- signal
# complémentaire à ``btc_cycles`` (cycle halving) et ``market_sentiment``
# (court/moyen terme technique). Extension à d'autres tags = décision opérateur.
DEFAULT_TAGS: list[str] = ["fed-rates"]


@dataclass
class PolymarketOutcome:
    label: str
    probability: float  # 0.0-1.0, prix du marché = probabilite implicite


@dataclass
class PolymarketEventSummary:
    available: bool
    title: str | None = None
    slug: str | None = None
    outcomes: list[PolymarketOutcome] = field(default_factory=list)
    volume_usd: float | None = None
    error: str | None = None


class PolymarketClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique sans clé)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 2.0) -> None:
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

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        logger.info("polymarket: echec appel -- %s", detail)

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    async def fetch_top_event_by_tag(self, tag_slug: str) -> PolymarketEventSummary:
        """Événement macro le plus liquide pour un tag donné (ex. `fed-rates`).

        Jamais de probabilité inventée : événement/marché introuvable ou données
        malformées -> `available=False`.
        """
        url = (
            f"{self.base_url}/events?limit=1&active=true&closed=false"
            f"&tag_slug={tag_slug}&order=volume&ascending=false"
        )
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            await asyncio.sleep(5.0)
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(url)
            except httpx.TransportError as exc2:
                self._record_failure(f"{url} -> {exc2}")
                return PolymarketEventSummary(available=False, error=f"{UNAVAILABLE} (timeout)")
        except Exception as exc:  # noqa: BLE001 -- une panne reseau ne doit jamais remonter
            self._record_failure(f"{url} -> {exc}")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return PolymarketEventSummary(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

        try:
            events = response.json()
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> reponse illisible")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        if not isinstance(events, list) or not events:
            self._record_failure(f"{url} -> aucun evenement pour ce tag")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        event = events[0]
        markets = event.get("markets") or []
        outcomes: list[PolymarketOutcome] = []
        for m in markets:
            question = m.get("question")
            raw_prices = m.get("outcomePrices")
            if not question or not raw_prices:
                continue
            try:
                # outcomePrices est une CHAINE JSON (pas une vraie liste) sur cet
                # endpoint -- verifie en direct le 10/07, ne jamais supposer le type.
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                prob = float(prices[0])  # prix du "Yes" -> probabilite implicite de la question.
            except (ValueError, TypeError, IndexError, json.JSONDecodeError):
                continue
            outcomes.append(PolymarketOutcome(label=question, probability=prob))

        if not outcomes:
            self._record_failure(f"{url} -> marches sans prix exploitables")
            return PolymarketEventSummary(available=False, error=UNAVAILABLE)

        self._record_success()
        return PolymarketEventSummary(
            available=True,
            title=event.get("title"),
            slug=event.get("slug"),
            outcomes=outcomes,
            volume_usd=float(event["volume"]) if event.get("volume") is not None else None,
        )


def format_polymarket_prompt_lines(events: list[dict]) -> list[str]:
    """Lignes compactes pour injection dans un prompt LLM (19/07) -- extrait depuis
    la logique inline de ``vc_analysis.py`` (jusque-là dupliquée en substance à
    chaque appelant) pour que ``momentum_entry.py`` bénéficie de la MÊME diligence
    macro que `/vc` sans réimplémenter le filtrage/troncature/sanitisation.

    Entrée : la forme produite par une boucle sur ``fetch_top_event_by_tag`` --
    ``[{"title": str, "outcomes": [{"label": str, "probability": float}, ...]}]``.
    3 outcomes max par événement (même plafond que ``vc_analysis.py``), jamais une
    probabilité inventée -- une entrée malformée est simplement ignorée, jamais une
    exception qui remonterait à l'appelant."""
    from aria_core.sanitize import sanitize_untrusted_text

    lines: list[str] = []
    for event in events:
        title = sanitize_untrusted_text(event.get("title") or "", 120)
        for outcome in (event.get("outcomes") or [])[:3]:
            label = sanitize_untrusted_text(outcome.get("label") or "", 160)
            prob = outcome.get("probability")
            if label and prob is not None:
                try:
                    lines.append(f"- [{title}] {label} : {float(prob):.0%}")
                except (TypeError, ValueError):
                    pass
    return lines


polymarket_client = PolymarketClient()
