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
# 23/07 -- ajoutés pour router la lecture X vers Tavily (moins cher que le repli
# x402 twit.sh) et pour l'extraction complète de site (Website/Docs Substance,
# jamais possible avec le snapshot 600 caractères de site_snapshot.py, conçu
# pour enrichir un prompt LLM, pas pour un audit de site). Authentification
# vérifiée en conditions réelles (23/07) : ``Authorization: Bearer <clé>``
# fonctionne sur LES TROIS endpoints (search/extract/crawl) -- utilisée ici
# pour ces deux nouveaux endpoints ; ``search()`` garde son authentification
# historique (clé dans le corps) inchangée, jamais retouchée sans raison.
EXTRACT_URL = "https://api.tavily.com/extract"
CRAWL_URL = "https://api.tavily.com/crawl"

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


@dataclass
class TavilyPage:
    """Une page extraite (par ``extract`` ou ``crawl``) -- contenu texte réel,
    jamais un résumé synthétique."""

    url: str
    title: str = ""
    raw_content: str = ""


@dataclass
class TavilyExtractResult:
    urls: list[str] = field(default_factory=list)
    pages: list[TavilyPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


@dataclass
class TavilyCrawlResult:
    root_url: str = ""
    pages: list[TavilyPage] = field(default_factory=list)
    available: bool = False
    error: str | None = None


def tavily_api_key() -> str:
    """Clé Tavily depuis l'env UNIQUEMENT (jamais en dur, jamais loguée)."""
    return os.environ.get("TAVILY_API_KEY", "").strip()


def is_tavily_configured() -> bool:
    return bool(tavily_api_key())


class TavilyClient:
    """Client HTTP async, lecture seule, throttle modéré."""

    # 21/07 -- calibré à 90% de 100 req/min confirmé (palier Development,
    # confirmé sur le dashboard réel de la clé "ARIA" -- type "dev", préfixe
    # "tvly-dev-" -- pas le palier Production à 1000/min). Doctrine CLAUDE.md
    # "Débit calibré à 90%" : 90/min = 0.667s. Remplace 0.5s (120/min), qui
    # dépassait déjà le plafond Dev réel.
    def __init__(self, *, min_interval: float = 0.667) -> None:
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

    async def _post(
        self, url: str, payload: dict, *, headers: dict | None = None, timeout: float = 15.0,
    ) -> tuple[object | None, str | None]:
        """POST générique avec la politique d'erreurs du dôme. Retourne (data, error).

        NB : la clé API (corps OU header ``Authorization``) n'est jamais loguée --
        on ne journalise que l'URL et le code d'erreur, jamais le payload/header."""
        attempt_429 = 0
        retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 apres {attempt_429} tentatives")
                    return None, f"{UNAVAILABLE} (rate limit)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur)"

            if response.status_code in (401, 403):
                # Clé absente/invalide : dégradation douce, on ne loggue jamais la clé.
                self._record_failure(f"{url} -> HTTP {response.status_code} (clé ?)")
                return None, f"{UNAVAILABLE} (clé refusée ou absente)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> HTTP {exc.response.status_code}")
                return None, f"{UNAVAILABLE} (HTTP {exc.response.status_code})"

            self._record_success()
            return response.json(), None

    async def _post_json(self, payload: dict) -> tuple[object | None, str | None]:
        """Repli historique de ``search()`` -- clé dans le corps, jamais retouché."""
        return await self._post(TAVILY_URL, payload)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 4,
        search_depth: str = "basic",
        include_answer: bool = True,
        include_domains: list[str] | None = None,
        caller: str = "unknown",
    ) -> TavilyResult:
        """Recherche Tavily pour une requête. Best-effort, jamais bloquant.

        ``include_domains`` (22/07) : restreint les résultats à des domaines précis --
        vérifié en direct que ``["twitter.com", "x.com"]`` renvoie de vrais résultats
        pertinents (posts/profils publics déjà indexés), sans passer par l'API X
        officielle ni le repli x402 (twit.sh). Portée honnête : indexation web
        classique, pas un flux temps réel -- adapté à de la veille, pas à une
        décision urgente.

        ``caller`` (22/07) : identifie qui dépense (ex. ``web_verify``,
        ``tavily_learning``) -- sert la traçabilité (``tavily_budget.recent_searches``),
        pas seulement le budget. Budget MENSUEL partagé (``tavily_budget.py``,
        900/1000 crédits) vérifié PROACTIVEMENT ici, avant tout appel HTTP réel --
        même doctrine que ``blockscout.py`` pour son budget Pro."""
        q = (query or "").strip()
        if not q:
            return TavilyResult(query=q, available=False, error="requête vide")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth = search_depth if search_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        credit_cost = tavily_budget.cost_for_search(depth)
        if not await tavily_budget.can_spend(credit_cost):
            return TavilyResult(query=q, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {
            "api_key": api_key,
            "query": q[:400],
            "search_depth": depth,
            "max_results": max(1, min(int(max_results), 10)),
            "include_answer": bool(include_answer),
        }
        if include_domains:
            payload["include_domains"] = list(include_domains)[:10]
        data, error = await self._post_json(payload)
        if error is not None:
            return TavilyResult(query=q, available=False, error=error)
        await tavily_budget.record_spend(caller=caller, query=q, credits=credit_cost)
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

    async def extract(
        self, urls: list[str], *, extract_depth: str = "basic", caller: str = "unknown",
    ) -> TavilyExtractResult:
        """Contenu texte RÉEL d'une ou plusieurs pages -- contrairement à ``search``
        (extraits DE TIERS à propos d'une page), ceci est le contenu de la page
        elle-même, rendu par l'infrastructure Tavily (gère le JS côté serveur --
        vérifié en conditions réelles le 23/07 : fonctionne sur une page X/Twitter
        SPA, que ``site_snapshot.py`` -- simple GET httpx -- ne saurait pas rendre).

        23/07, #routage lecture X vers Tavily + Website/Docs Substance -- REMPLACE
        ``twit.sh`` (x402, payant par appel) pour les profils X quand Tavily est
        configuré, et remplace le snapshot 600 caractères de ``site_snapshot.py``
        pour les signaux de substance (celui-ci reste inchangé pour son usage
        historique -- enrichir le prompt LLM, pas un audit)."""
        clean_urls = [u.strip() for u in (urls or []) if u and u.strip()][:20]
        if not clean_urls:
            return TavilyExtractResult(available=False, error="aucune URL fournie")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth = extract_depth if extract_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        credit_cost = tavily_budget.cost_for_extract(depth, len(clean_urls))
        if not await tavily_budget.can_spend(credit_cost):
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {"urls": clean_urls, "extract_depth": depth}
        headers = {"Authorization": f"Bearer {api_key}"}
        data, error = await self._post(EXTRACT_URL, payload, headers=headers, timeout=25.0)
        if error is not None:
            return TavilyExtractResult(urls=clean_urls, available=False, error=error)
        await tavily_budget.record_spend(caller=caller, query=f"extract:{clean_urls[0]}", credits=credit_cost)
        if not isinstance(data, dict):
            return TavilyExtractResult(urls=clean_urls, available=False, error=UNAVAILABLE)

        pages: list[TavilyPage] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("raw_content") or "").strip()
            if not content:
                continue
            pages.append(
                TavilyPage(url=str(item.get("url") or ""), title=str(item.get("title") or ""), raw_content=content)
            )

        if not pages:
            return TavilyExtractResult(urls=clean_urls, available=False, error=f"{UNAVAILABLE} (aucune page exploitable)")
        return TavilyExtractResult(urls=clean_urls, pages=pages, available=True, error=None)

    async def crawl(
        self, root_url: str, *, max_depth: int = 2, limit: int = 15,
        extract_depth: str = "basic", caller: str = "unknown",
    ) -> TavilyCrawlResult:
        """Parcourt un site à partir de ``root_url`` (suit les liens internes,
        y compris les sous-domaines comme ``docs.<site>``) et renvoie le contenu
        texte réel de chaque page trouvée -- seule façon de « tout extraire pour
        noter » un site multi-pages (demande opérateur explicite, 23/07) ; le
        snapshot homepage-only 600 caractères de ``site_snapshot.py`` ne couvre
        jamais les sous-pages (Docs/Team/Tokenomics...).

        Coût variable (dépend du nombre RÉEL de pages renvoyées, connu seulement
        après l'appel) -- vérification de budget AVANT l'appel sur le PIRE CAS
        (``limit`` pages, Tavily n'en renvoie jamais plus), dépense RÉELLE
        enregistrée après coup sur le nombre de pages effectivement reçues."""
        url = (root_url or "").strip()
        if not url:
            return TavilyCrawlResult(available=False, error="URL racine vide")

        api_key = tavily_api_key()
        if not api_key:
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (TAVILY_API_KEY absente)")

        depth_param = max(1, min(int(max_depth), 3))
        page_limit = max(1, min(int(limit), 30))
        extract_d = extract_depth if extract_depth in ("basic", "advanced") else "basic"

        from aria_core.services import tavily_budget

        worst_case = tavily_budget.estimate_crawl_worst_case(extract_d, page_limit)
        if not await tavily_budget.can_spend(worst_case):
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (budget mensuel épuisé)")

        payload = {
            "url": url, "max_depth": depth_param, "limit": page_limit, "extract_depth": extract_d,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        data, error = await self._post(CRAWL_URL, payload, headers=headers, timeout=60.0)
        if error is not None:
            return TavilyCrawlResult(root_url=url, available=False, error=error)
        if not isinstance(data, dict):
            await tavily_budget.record_spend(caller=caller, query=f"crawl:{url}", credits=0)
            return TavilyCrawlResult(root_url=url, available=False, error=UNAVAILABLE)

        pages: list[TavilyPage] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("raw_content") or "").strip()
            if not content:
                continue
            pages.append(
                TavilyPage(url=str(item.get("url") or ""), title=str(item.get("title") or ""), raw_content=content)
            )

        real_cost = tavily_budget.cost_for_crawl(extract_d, len(pages))
        await tavily_budget.record_spend(caller=caller, query=f"crawl:{url}", credits=real_cost)

        if not pages:
            return TavilyCrawlResult(root_url=url, available=False, error=f"{UNAVAILABLE} (aucune page exploitable)")
        return TavilyCrawlResult(root_url=url, pages=pages, available=True, error=None)


tavily_client = TavilyClient()
