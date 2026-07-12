"""Client de recherche web Tavily — fournisseur fiable pour la vérification de faits.

Alternative à DuckDuckGo (qui renvoie des 403 systématiques sans throttle/backoff) pour
le point d'entrée unique `web_verify.fetch_web_snippets`. Tavily est orienté LLM : il
renvoie des extraits courts déjà taillés pour de la vérification de faits.

Doctrine « dôme » (identique à goplus.py / blockscout.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (liste vide, jamais une
  donnée inventée).
- La clé API vit UNIQUEMENT dans l'environnement (`TAVILY_API_KEY`) — jamais en dur, jamais
  loguée. Sans clé, le client est simplement `available=False` et ne fait aucun appel.

Lecture seule. Gaté en amont par `settings.aria_web_search_provider == "tavily"` : tant que
l'opérateur n'a pas basculé le flag ET fourni une clé, DuckDuckGo reste le fournisseur.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

UNAVAILABLE = "donnée Tavily indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class TavilyResult:
    """Extraits web renvoyés par Tavily. `available=False` + `error` si indisponible ;
    jamais de donnée inventée."""

    query: str
    # Chaque snippet : (text, url, published_date brut ou None -- Tavily ne le fournit pas
    # toujours, surtout hors recherche "news"; cf. #126).
    snippets: list[tuple[str, str, str | None]] = field(default_factory=list)
    # Réponse synthétique optionnelle de Tavily (include_answer).
    answer: str | None = None
    available: bool = False
    error: str | None = None


def tavily_api_key() -> str:
    """Clé Tavily depuis l'env UNIQUEMENT (jamais en dur, jamais loguée)."""
    return os.environ.get("TAVILY_API_KEY", "").strip()


def is_tavily_configured() -> bool:
    return bool(tavily_api_key())


class TavilyClient:
    """Client HTTP async, lecture seule, throttle modéré."""

    def __init__(self, *, min_interval: float = 0.5) -> None:
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
                "tavily: %s echecs consecutifs (dernier: %s) — pas de blocage",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "tavily: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _post_json(self, payload: dict) -> tuple[object | None, str | None]:
        """POST avec la politique d'erreurs du dôme. Retourne (data, error).

        NB : la clé API est dans le corps de la requête — jamais loguée (on ne journalise
        que l'URL et le code d'erreur, jamais le payload)."""
        attempt_429 = 0
        retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(TAVILY_URL, json=payload)
            except httpx.TransportError as exc:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{TAVILY_URL} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{TAVILY_URL} -> HTTP 429 apres {attempt_429} tentatives")
                    return None, f"{UNAVAILABLE} (rate limit)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{TAVILY_URL} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur)"

            if response.status_code in (401, 403):
                # Clé absente/invalide : dégradation douce, on ne loggue jamais la clé.
                self._record_failure(f"{TAVILY_URL} -> HTTP {response.status_code} (clé ?)")
                return None, f"{UNAVAILABLE} (clé refusée ou absente)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{TAVILY_URL} -> HTTP {exc.response.status_code}")
                return None, f"{UNAVAILABLE} (HTTP {exc.response.status_code})"

            self._record_success()
            return response.json(), None

    async def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        search_depth: str = "basic",
        include_answer: bool = True,
    ) -> TavilyResult:
        """Recherche Tavily pour une requête. Best-effort, jamais bloquant."""
        q = (query or "").strip()
        if not q:
            return TavilyResult(query=q, available=False, error="requête vide")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        payload = {
            "api_key": api_key,
            "query": q[:400],
            "search_depth": search_depth if search_depth in ("basic", "advanced") else "basic",
            "max_results": max(1, min(int(max_results), 10)),
            "include_answer": bool(include_answer),
        }
        data, error = await self._post_json(payload)
        if error is not None:
            return TavilyResult(query=q, available=False, error=error)
        if not isinstance(data, dict):
            return TavilyResult(query=q, available=False, error=UNAVAILABLE)

        snippets: list[tuple[str, str, str | None]] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("content") or item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            published = item.get("published_date")
            published = str(published).strip() if published else None
            if len(text) >= 15:
                snippets.append((text[:280], url, published))

        answer = data.get("answer")
        answer = str(answer).strip() if answer else None

        if not snippets and not answer:
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (aucun résultat)")

        return TavilyResult(query=q, snippets=snippets, answer=answer, available=True, error=None)


tavily_client = TavilyClient()
