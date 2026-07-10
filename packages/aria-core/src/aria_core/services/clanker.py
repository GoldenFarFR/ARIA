"""Client de lecture seule Clanker (Base) — launchpad « direct » (sans phase de bonding).

Contrairement à Virtuals (courbe de bonding puis graduation), un token Clanker reçoit
une **vraie liquidité DEX dès son déploiement** (fair-launch Uniswap v3/v4 sur Base) —
il n'y a pas de phase pré-graduation à distinguer. Ce client sert donc uniquement à la
**découverte rapide** (les tokens les plus récents) ; l'absorption elle-même réutilise
le pipeline standard (`token_absorber.absorb`, pool 85% VC), pas un chemin dédié comme
la niche bonding.

Endpoint public documenté (``github.com/clanker-devco/DOCS``) : base
``https://www.clanker.world/api``, ``GET /api/tokens`` (« Search and list Clanker
tokens with filters, sorting, and cursor-based pagination »). Auth : aucune pour la
lecture publique (clé partenaire ``x-api-key`` uniquement pour des quotas plus élevés,
non requise ici).

Paramètres de requête (``chainId``/``sort``/``sortBy``/``limit``) et forme de la
réponse CONFIRMÉS EN DIRECT depuis le VPS le 10/07 (le sandbox cloud, lui, était
bloqué en HTTP 403 — anti-bot générique côté Cloudflare, comportement propre à cet
environnement, pas à l'API). Deux corrections faites grâce aux messages de validation
de l'API elle-même (jamais de la doc, muette sur ces détails) : ``sortBy`` accepte une
énumération stricte (cf. ``_VALID_SORT_BY`` — ``createdAt``, plausible, était faux) ;
``limit`` a un plafond RÉEL de 20 (cf. ``_MAX_LIMIT`` — 100, plausible, était faux et
faisait échouer TOUT l'appel en HTTP 400, pas juste sous-optimal). Réponse valide :
``{"data": [...]}``, chaque item un dict à plat en ``snake_case`` (``id``,
``created_at``, ``admin`` — le déployeur —, ``tx_hash``, ``contract_address``,
``name``, ``symbol``, ``description``, ``deployed_at``, ``starting_market_cap``,
``chain_id``, ``platform``...). Le parsing ci-dessous reste délibérément tolérant
(``_first`` sur plusieurs noms de champs, snake_case ET camelCase) pour dégrader
proprement si la forme évolue, plutôt que de figer une dépendance fragile.

Mêmes politiques que les autres clients de ce dossier :
- Aucune écriture, aucune signature, GET uniquement.
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer.
- Timeout / 5xx : 1 retry après 5s, puis fallback explicite.
- ``fetch_*`` ne lève JAMAIS sur erreur réseau : renvoient ``[]`` / ``None``.
- Dôme : toute chaîne externe passe par ``_sanitize`` (retrait des caractères de
  contrôle + neutralisation des chevrons ``<``/``>`` — anti prompt-injection).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://www.clanker.world/api"
_TOKENS_ENDPOINT = f"{API_ROOT}/tokens"

UNAVAILABLE = "donnée Clanker indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FIELD_MAX = 600


@dataclass
class ClankerToken:
    """Un token Clanker indexé. Toutes les chaînes sont déjà sanitisées."""

    name: str | None = None
    symbol: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    pool_address: str | None = None
    created_at: str | None = None
    mcap: float | None = None
    volume24h: float | None = None
    liquidity_usd: float | None = None
    holder_count: int | None = None
    deployer_address: str | None = None
    description: str | None = None
    warning_flags: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Sanitisation (dôme) — identique à services/virtuals.py
# ----------------------------------------------------------------------
def _sanitize(text: object, max_len: int = _FIELD_MAX) -> str | None:
    if text is None:
        return None
    s = _CONTROL_CHARS_RE.sub("", str(text))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or isinstance(value, (list, dict)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first(mapping: dict, *keys: str) -> object:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


# ----------------------------------------------------------------------
# Construction d'URL
# ----------------------------------------------------------------------
#: Valeurs acceptées par ``sortBy``, CONFIRMÉES EN DIRECT le 10/07 depuis le VPS (le
#: endpoint renvoie une erreur de validation explicite listant l'énumération exacte
#: quand on envoie une valeur invalide — ``createdAt``, plausible mais faux, a été
#: corrigé grâce à ce message d'erreur). Seule ``deployed-at`` correspond à « le plus
#: récent d'abord », ce qu'on veut pour la découverte.
_VALID_SORT_BY = frozenset(
    {"market-cap", "tx-h24", "volume-h24", "price-percent-h24", "price-percent-h1", "deployed-at"}
)


#: Plafond RÉEL de ``limit``, CONFIRMÉ EN DIRECT le 10/07 depuis le VPS : un appel
#: avec ``limit=50`` a échoué en HTTP 400 avec un message de validation explicite
#: (``"maximum":20,"inclusive":true,"path":["limit"]``) — 100 (supposition initiale)
#: était faux. Un ``limit`` hors bornes n'est plus juste "sous-optimal", il fait
#: échouer TOUT l'appel (fetch_recent renvoie ``[]``) : ce plafond doit rester exact.
_MAX_LIMIT = 20


def build_recent_tokens_url(chain_id: int = 8453, limit: int = 50) -> str:
    """URL des tokens les plus récents sur Base (``chain_id=8453``).

    ``chainId``/``sort``/``sortBy``/``limit`` (``<=20``) CONFIRMÉS EN DIRECT le 10/07
    (VPS, accès réseau réel — le sandbox cloud était bloqué en HTTP 403 anti-bot). La
    forme d'une réponse VALIDE (``limit`` conforme) a aussi été confirmée en direct :
    ``{"data": [...]}`` où chaque item est un dict à plat (``id``, ``created_at``,
    ``admin``, ``tx_hash``, ``contract_address``, ``name``, ``symbol``,
    ``description``, ...) — voir ``parse_clanker_token`` pour les champs retenus.
    """
    try:
        size = int(limit)
    except (TypeError, ValueError):
        size = _MAX_LIMIT
    size = max(1, min(size, _MAX_LIMIT))
    params = [
        ("chainId", str(chain_id)),
        ("sort", "desc"),
        ("sortBy", "deployed-at"),
        ("limit", str(size)),
    ]
    return f"{_TOKENS_ENDPOINT}?{urlencode(params)}"


def build_token_by_address_url(token_address: str, chain_id: int = 8453) -> str:
    """URL de filtre par adresse de contrat."""
    params = [("chainId", str(chain_id)), ("address", str(token_address))]
    return f"{_TOKENS_ENDPOINT}?{urlencode(params)}"


# ----------------------------------------------------------------------
# Parsing (dégradation gracieuse)
# ----------------------------------------------------------------------
def parse_clanker_token(raw: dict) -> ClankerToken | None:
    """Parse un objet de réponse Clanker en ``ClankerToken``. Jamais d'exception.

    Champs réels CONFIRMÉS EN DIRECT le 10/07 (``contract_address``, ``admin``,
    ``created_at``/``deployed_at``, ``starting_market_cap`` — snake_case) placés en
    tête de chaque liste de fallback ; variantes camelCase gardées en repli tolérant
    au cas où la forme évolue. Raw non-dict → ``None`` ; champ manquant → ``None``
    (facts-only) — ``volume24h``/``liquidity_usd``/``holder_count`` restent
    généralement ``None`` sur cet endpoint (feed de déploiement, pas de données de
    marché live), sans qu'aucune donnée manquante ne soit jamais inventée.
    """
    if not isinstance(raw, dict):
        return None

    return ClankerToken(
        name=_sanitize(_first(raw, "name", "tokenName"), 120),
        symbol=_sanitize(_first(raw, "symbol", "ticker"), 20),
        chain_id=_safe_int(_first(raw, "chain_id", "chainId")),
        contract_address=_sanitize(
            _first(raw, "contract_address", "contractAddress", "address", "tokenAddress"), 80
        ),
        pool_address=_sanitize(_first(raw, "pool_address", "poolAddress", "pair"), 80),
        created_at=_sanitize(_first(raw, "created_at", "createdAt", "deployed_at", "deployedAt"), 40),
        mcap=_safe_float(_first(raw, "starting_market_cap", "marketCap", "mcap", "market_cap")),
        volume24h=_safe_float(_first(raw, "volume24h", "volume_24h", "volume")),
        liquidity_usd=_safe_float(_first(raw, "liquidityUsd", "liquidity_usd", "liquidity")),
        holder_count=_safe_int(_first(raw, "holderCount", "holder_count", "holders")),
        deployer_address=_sanitize(
            _first(raw, "admin", "msg_sender", "deployer_address", "deployerAddress", "creator", "deployer"), 80
        ),
        description=_sanitize(_first(raw, "description"), _FIELD_MAX),
        warning_flags=[
            _sanitize(f, 200) for f in (raw.get("warnings") or []) if isinstance(f, (str, int, float))
        ][:12],
    )


# ----------------------------------------------------------------------
# Client HTTP (lecture seule)
# ----------------------------------------------------------------------
class ClankerClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique)."""

    def __init__(self, endpoint: str = _TOKENS_ENDPOINT, *, min_interval: float = 0.5) -> None:
        self.endpoint = endpoint
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
                "clanker: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "clanker: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        """GET avec la politique d'erreurs maison. Retourne ``(data, error)``."""
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
                return None, f"{UNAVAILABLE} (timeout Clanker)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Clanker)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Clanker)"

            if response.status_code in (403, 404):
                # 403 : probable blocage anti-bot générique (constaté en test) — pas
                # une escalade, une dégradation documentée (cf. avertissement module).
                self._record_success()
                return None, f"{UNAVAILABLE} (HTTP {response.status_code})"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def fetch_recent(self, chain_id: int = 8453, limit: int = 50) -> list[ClankerToken]:
        """Tokens les plus récents sur Base. Toujours une liste (``[]`` sur erreur)."""
        try:
            url = build_recent_tokens_url(chain_id=chain_id, limit=limit)
            data, error = await self._get_json(url)
            if error is not None:
                return []
            items = None
            if isinstance(data, dict):
                items = _first(data, "tokens", "data", "results")
            elif isinstance(data, list):
                items = data
            if not isinstance(items, list):
                return []
            tokens: list[ClankerToken] = []
            for item in items:
                token = parse_clanker_token(item)
                if token is not None:
                    tokens.append(token)
            return tokens
        except Exception as exc:  # dégradation ultime : jamais d'exception sortante
            logger.info("clanker: fetch_recent echec inattendu — %s", exc)
            return []

    async def fetch_by_address(self, token_address: str, chain_id: int = 8453) -> ClankerToken | None:
        """Token Clanker par adresse de contrat. ``None`` sur erreur ou absence."""
        try:
            url = build_token_by_address_url(token_address, chain_id=chain_id)
            data, error = await self._get_json(url)
            if error is not None:
                return None
            items = None
            if isinstance(data, dict):
                items = _first(data, "tokens", "data", "results")
            elif isinstance(data, list):
                items = data
            if not isinstance(items, list) or not items:
                return None
            return parse_clanker_token(items[0])
        except Exception as exc:
            logger.info("clanker: fetch_by_address echec inattendu — %s", exc)
            return None


clanker_client = ClankerClient()
