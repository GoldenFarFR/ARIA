"""Client de lecture seule Virtuals Protocol (Base) — détection pré-bonding.

Cœur de la niche ARIA : indexer les tokens Virtuals **encore en courbe de
bonding** (statut Strapi ``UNDERGRAD`` = Prototype) avant leur graduation
(``AVAILABLE`` = Sentient, seuil 42 000 VIRTUAL accumulés). Endpoint Strapi
public, sans clé (cf. ``docs/recherche-launchpads-base-2026.md``).

Aucune écriture, aucune signature, aucun appel autre que GET. Même politique
d'erreurs et de dégradation que ``services/blockscout.py`` / ``services/coingecko.py`` :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer.
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- **Ne lève JAMAIS sur erreur réseau** : ``fetch_*`` renvoient ``[]`` / ``None``.
- Aucune donnée manquante n'est remplacée par une supposition (facts-only) :
  champ absent → ``None``, jamais une valeur inventée.

## Dôme de sécurité (données externes hostiles)

TOUTES les chaînes issues de l'API (nom, symbole, description, statut,
adresses, liens sociaux) sont **non fiables** et passent par ``_sanitize`` :
retrait des caractères de contrôle + **neutralisation des chevrons** ``<`` / ``>``
(remplacés par ``‹`` / ``›``), pour qu'un nom/symbole hostile ne puisse pas
forger une balise délimitante et s'échapper d'une zone non fiable en aval
(anti prompt-injection — même helper que ``skills/vc_analysis.py``). Les liens
sociaux sont en plus restreints au schéma ``http(s)`` uniquement.

⚠️ Réseau bloqué dans l'environnement de build : les appels live échouent et
sont testés sur fixtures ; branchement live sur le VPS (cf. doc de recherche —
`curl` de confirmation + re-vérification des adresses BaseScan avant prod).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)

# Index des tokens (index pré-bonding). NE PAS confondre avec le registre ACP
# `acpx.virtuals.io/api/agents` (agents de service) — cf. doc de recherche §5.
API_ROOT = "https://api.virtuals.io/api"
_VIRTUALS_ENDPOINT = f"{API_ROOT}/virtuals"

# Seuil de graduation : 42 000 VIRTUAL accumulés dans la courbe (doc de recherche).
GRADUATION_THRESHOLD_VIRTUAL = 42_000.0

UNAVAILABLE = "donnée Virtuals indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

# Dôme : mêmes défenses que skills/vc_analysis.py (point d'étranglement unique).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FIELD_MAX = 600

# Statuts « encore en bonding » — ALLOWLIST stricte (forme texte préférée à la
# forme numérique inférée du frontend ; « 1 » = prototype). Tout le reste
# (AVAILABLE/gradué, inconnu, None) → PAS en bonding (conservateur).
_BONDING_STATUSES = frozenset({"UNDERGRAD", "PROTOTYPE", "1"})

# Champs porteurs des VIRTUAL accumulés dans la courbe. VÉRIFIÉ EN DIRECT le
# 10/07 (contrat 0x6f8c2Eb5..., payload complet inspecté) : AUCUN de ces noms
# n'existe dans la réponse API réelle -- graduation_progress() renvoie donc
# toujours None en pratique aujourd'hui (dégradation honnête, jamais un chiffre
# inventé -- cf. le "56,94%" affiché par l'UI Virtuals, dont la vraie formule
# n'est pas confirmée : ni mcapInVirtual/42000 ni totalValueLocked ne
# reproduisent exactement ce nombre, donc pas de proxy fiable à câbler tant
# que ce n'est pas confirmé). Gardés en whitelist au cas où l'API les
# exposerait un jour (ou pour un token différent) -- coût nul, jamais de faux
# positif puisque absents = None.
_VIRTUAL_RAISED_KEYS = (
    "virtualRaised",
    "raisedVirtual",
    "virtual_raised",
    "bondingCurveVirtualReserve",
)


@dataclass
class VirtualToken:
    """Un token Virtuals indexé. Toutes les chaînes sont déjà sanitisées."""

    name: str | None = None
    symbol: str | None = None
    status: str | None = None
    chain: str | None = None
    token_address: str | None = None
    pre_token_address: str | None = None
    created_at: str | None = None
    mcap: float | None = None
    volume24h: float | None = None
    price_change24h: float | None = None
    holder_count: int | None = None
    description: str | None = None
    socials: list[dict] = field(default_factory=list)
    raw_status: str | None = None
    # Extras dérivés (au-delà du strict minimum), facts-only :
    virtual_id: int | None = None  # id Strapi de l'entité (pour build_token_url)
    virtual_raised: float | None = None  # VIRTUAL accumulés, si l'API l'expose


# ----------------------------------------------------------------------
# Sanitisation (dôme)
# ----------------------------------------------------------------------
def _sanitize(text: object, max_len: int = _FIELD_MAX) -> str | None:
    """Neutralise une chaîne externe. ``None`` reste ``None`` (facts-only).

    Retire les caractères de contrôle, neutralise les chevrons ``<`` / ``>``
    (aucun usage légitime dans des métadonnées on-chain), tronque à ``max_len``.
    """
    if text is None:
        return None
    s = _CONTROL_CHARS_RE.sub("", str(text))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


# ----------------------------------------------------------------------
# Coercition numérique prudente (jamais de supposition)
# ----------------------------------------------------------------------
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
    """Première valeur non-``None`` parmi ``keys`` (tolère les variantes de nommage)."""
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


# ----------------------------------------------------------------------
# Construction d'URL (endpoints Strapi publics)
# ----------------------------------------------------------------------
def build_prototypes_url(chain: str = "BASE", page_size: int = 100, *, status: str = "UNDERGRAD") -> str:
    """URL de l'index filtré par statut, trié par récence.

    ``status`` par défaut ``"UNDERGRAD"`` (encore en bonding, comportement historique
    inchangé). ``build_graduated_url`` réutilise cette même fonction avec
    ``status="AVAILABLE"`` (tokens gradués) — un seul point de construction d'URL,
    jamais dupliqué.

    Ex. ``…/api/virtuals?filters[status]=UNDERGRAD&filters[chain]=BASE&sort[0]=createdAt:desc&pagination[pageSize]=100``
    """
    try:
        size = int(page_size)
    except (TypeError, ValueError):
        size = 100
    size = max(1, min(size, 200))
    params = [
        ("filters[status]", str(status)),
        ("filters[chain]", str(chain).upper()),
        ("sort[0]", "createdAt:desc"),
        ("pagination[pageSize]", str(size)),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


def build_graduated_url(chain: str = "BASE", page_size: int = 100) -> str:
    """URL des tokens ayant gradué récemment (statut ``AVAILABLE``).

    ``sort[0]=createdAt:desc`` reste trié par date de CRÉATION du prototype (pas de
    date de graduation exposée par l'API) — les plus récemment créés apparaissent en
    tête, ce qui capture en pratique les graduations récentes sans être une garantie
    stricte d'ordre de graduation.
    """
    return build_prototypes_url(chain=chain, page_size=page_size, status="AVAILABLE")


def build_token_url(virtual_id: object) -> str:
    """URL du détail d'un token par son id Strapi (``…/api/virtuals/{id}``)."""
    return f"{_VIRTUALS_ENDPOINT}/{quote(str(virtual_id), safe='')}"


def build_token_by_address_url(token_address: str, chain: str = "BASE") -> str:
    """URL de filtre par adresse de token (``filters[tokenAddress][$eq]=0x…``).

    Adresse mise en minuscules avant le filtre : diagnostic réel (10/07,
    contrats 0x6f8c2Eb5.../0xB455C23d... testés en direct) -- un filtre EXACT
    (`$eq`) sur une adresse en casse mixte (le format habituel copié depuis un
    explorateur/wallet) ne matche pas si l'API Virtuals stocke l'adresse en
    minuscules, faisant échouer silencieusement TOUTE détection de bonding
    (`_resolve_bonding_phase`) -- retombée sur l'analyse générique "aucune
    paire". Les adresses EVM sont insensibles à la casse (le mélange n'existe
    que pour le checksum d'affichage), donc aucune perte d'information.
    """
    params = [
        ("filters[tokenAddress][$eq]", str(token_address).lower()),
        ("filters[chain]", str(chain).upper()),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


def build_token_by_pretoken_url(pre_token_address: str, chain: str = "BASE") -> str:
    """URL de filtre par adresse PRE-TOKEN (``filters[preToken][$eq]=0x…``).

    Diagnostic réel (10/07, contrat 0x6f8c2Eb5... testé en direct) : ``tokenAddress``
    reste ``null`` TANT QU'un token n'a pas gradué -- c'est structurel, pas une
    panne. L'adresse de contrat que voit l'opérateur pendant la phase de bonding
    (celle affichée sur virtuals.io, celle qu'il colle dans `/vc`) est stockée
    dans ``preToken``, jamais ``tokenAddress``. Sans ce filtre de repli,
    ``fetch_by_address`` ne pouvait STRUCTURELLEMENT jamais trouver un token
    encore en bonding par son adresse -- exactement la catégorie que
    ``_resolve_bonding_phase`` est censée détecter."""
    params = [
        ("filters[preToken][$eq]", str(pre_token_address).lower()),
        ("filters[chain]", str(chain).upper()),
    ]
    return f"{_VIRTUALS_ENDPOINT}?{urlencode(params, safe='[]:$')}"


# ----------------------------------------------------------------------
# Extraction des liens sociaux (http/https uniquement, sanitisés)
# ----------------------------------------------------------------------
def _extract_socials(attrs: dict) -> list[dict]:
    """Liens sociaux vérifiables uniquement (schéma ``http(s)``), dédupliqués.

    Tolère les formes Strapi/Virtuals rencontrées : ``socials`` en dict
    (éventuellement imbriqué, ex. ``{"VERIFIED_LINKS": {"TWITTER": "https://…"}}``),
    en liste de ``{label,url}``, plus les clés directes (``twitter``…). Toute
    URL non ``http(s)`` (ex. ``javascript:``) est rejetée.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(label: object, url: object) -> None:
        if not isinstance(url, str):
            return
        candidate = url.strip()
        if not candidate.lower().startswith(("http://", "https://")):
            return
        clean_url = _sanitize(candidate, 300)
        if not clean_url or clean_url in seen:
            return
        seen.add(clean_url)
        out.append({"label": _sanitize(label, 40) or "", "url": clean_url})

    raw = attrs.get("socials")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, str):
                _add(key, value)
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    _add(sub_key, sub_value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _add(
                            _first(item, "label", "type", "name", "platform"),
                            _first(item, "url", "value", "href", "link"),
                        )
                    else:
                        _add("", item)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                _add(
                    _first(item, "label", "type", "name", "platform"),
                    _first(item, "url", "value", "href", "link"),
                )
            else:
                _add("", item)

    for key in ("twitter", "telegram", "website", "discord", "warpcast"):
        _add(key, attrs.get(key))

    return out[:12]


# ----------------------------------------------------------------------
# Parsing d'un objet de réponse (dégradation gracieuse)
# ----------------------------------------------------------------------
def parse_virtual(raw: dict) -> VirtualToken | None:
    """Parse un objet Strapi en ``VirtualToken``. Jamais d'exception.

    Tolère la forme ``{"id":…, "attributes": {…}}`` (Strapi v4) ET la forme
    plate. Raw non-dict → ``None`` ; champ manquant → ``None`` (facts-only).
    """
    if not isinstance(raw, dict):
        return None

    attributes = raw.get("attributes")
    attrs = attributes if isinstance(attributes, dict) else raw

    raw_status = _first(attrs, "status")

    virtual_raised = None
    for key in _VIRTUAL_RAISED_KEYS:
        virtual_raised = _safe_float(attrs.get(key))
        if virtual_raised is not None:
            break

    return VirtualToken(
        name=_sanitize(_first(attrs, "name"), 120),
        symbol=_sanitize(_first(attrs, "symbol", "ticker"), 20),
        status=_sanitize(raw_status, 40),
        chain=_sanitize(_first(attrs, "chain"), 20),
        token_address=_sanitize(_first(attrs, "tokenAddress", "token_address", "contractAddress"), 80),
        pre_token_address=_sanitize(_first(attrs, "preToken", "preTokenAddress", "pre_token_address"), 80),
        created_at=_sanitize(_first(attrs, "createdAt", "created_at"), 40),
        mcap=_safe_float(_first(attrs, "mcapInVirtual", "mcap", "marketCap", "market_cap")),
        volume24h=_safe_float(_first(attrs, "volume24h", "volume_24h", "volume")),
        price_change24h=_safe_float(
            _first(attrs, "priceChangePercent24h", "priceChange24h", "price_change_24h")
        ),
        holder_count=_safe_int(_first(attrs, "holderCount", "holder_count", "holders")),
        description=_sanitize(_first(attrs, "description"), _FIELD_MAX),
        socials=_extract_socials(attrs),
        raw_status=_sanitize(raw_status, 40),
        virtual_id=_safe_int(raw.get("id") if raw.get("id") is not None else attrs.get("id")),
        virtual_raised=virtual_raised,
    )


# ----------------------------------------------------------------------
# Prédicats facts-only
# ----------------------------------------------------------------------
def is_in_bonding(token: VirtualToken) -> bool:
    """True uniquement si le statut est dans l'ALLOWLIST de bonding.

    ``UNDERGRAD`` / ``PROTOTYPE`` / ``1`` → True. ``AVAILABLE`` (gradué),
    statut inconnu ou absent → False (conservateur : on n'affirme le bonding
    que sur un statut connu).
    """
    for candidate in (token.status, token.raw_status):
        if candidate is None:
            continue
        if str(candidate).strip().upper() in _BONDING_STATUSES:
            return True
    return False


def graduation_progress(token: VirtualToken) -> float | None:
    """Progression vers la graduation (0.0–1.0) si dérivable, sinon ``None``.

    Ratio ``VIRTUAL accumulés / 42 000``. Renvoie ``None`` quand l'API n'expose
    pas la valeur accumulée (facts-only : pas d'inférence depuis la mcap, qui
    n'est pas la réserve de courbe).
    """
    raised = token.virtual_raised
    if raised is None or raised < 0:
        return None
    return min(raised / GRADUATION_THRESHOLD_VIRTUAL, 1.0)


# ----------------------------------------------------------------------
# Client HTTP (lecture seule)
# ----------------------------------------------------------------------
class VirtualsClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique sans clé).

    ``httpx.AsyncClient`` par défaut (``trust_env=True``) : le bundle CA et le
    proxy éventuels sont récupérés de l'environnement, comme les autres clients
    du dossier ``services/``.
    """

    def __init__(self, endpoint: str = _VIRTUALS_ENDPOINT, *, min_interval: float = 0.5) -> None:
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
                "virtuals: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "virtuals: echec appel (%s/%s) — %s",
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
                return None, f"{UNAVAILABLE} (timeout Virtuals)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Virtuals)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Virtuals)"

            if response.status_code == 404:
                self._record_success()
                return None, "token Virtuals introuvable"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def fetch_prototypes(self, chain: str = "BASE", page_size: int = 100) -> list[VirtualToken]:
        """Index des tokens encore en bonding. Toujours une liste (``[]`` sur erreur)."""
        try:
            url = build_prototypes_url(chain=chain, page_size=page_size)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return []
            items = data.get("data")
            if not isinstance(items, list):
                return []
            tokens: list[VirtualToken] = []
            for item in items:
                token = parse_virtual(item)
                if token is not None:
                    tokens.append(token)
            return tokens
        except Exception as exc:  # dégradation ultime : jamais d'exception sortante
            logger.info("virtuals: fetch_prototypes echec inattendu — %s", exc)
            return []

    async def fetch_graduated(self, chain: str = "BASE", page_size: int = 100) -> list[VirtualToken]:
        """Tokens ayant récemment gradué (statut ``AVAILABLE``). Toujours une liste.

        Ces tokens ont une vraie liquidité DEX (post-graduation) : ils rejoignent le
        pipeline d'absorption STANDARD (85% VC), pas la niche bonding — cf.
        ``services/launchpad_discovery.py``.
        """
        try:
            url = build_graduated_url(chain=chain, page_size=page_size)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return []
            items = data.get("data")
            if not isinstance(items, list):
                return []
            tokens: list[VirtualToken] = []
            for item in items:
                token = parse_virtual(item)
                if token is not None:
                    tokens.append(token)
            return tokens
        except Exception as exc:  # dégradation ultime : jamais d'exception sortante
            logger.info("virtuals: fetch_graduated echec inattendu — %s", exc)
            return []

    async def fetch_virtual(self, virtual_id: object) -> VirtualToken | None:
        """Détail d'un token par id Strapi. ``None`` sur erreur (jamais d'exception)."""
        try:
            url = build_token_url(virtual_id)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return None
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            return parse_virtual(payload)
        except Exception as exc:
            logger.info("virtuals: fetch_virtual echec inattendu — %s", exc)
            return None

    async def fetch_by_address(self, token_address: str, chain: str = "BASE") -> VirtualToken | None:
        """Token Virtuals par adresse de contrat (ce que reçoit `/vc <contrat>`, jamais
        l'id Strapi interne). ``None`` sur erreur ou absence — jamais d'exception.

        Essaie d'abord ``tokenAddress`` (token gradué). Si rien ne matche, retente
        via ``preToken`` (token encore en bonding -- ``tokenAddress`` y est
        toujours ``null``, cf. ``build_token_by_pretoken_url``). Un second appel
        réseau uniquement dans ce cas de repli, jamais sur le chemin heureux."""
        try:
            url = build_token_by_address_url(token_address, chain=chain)
            data, error = await self._get_json(url)
            if error is None and isinstance(data, dict):
                items = data.get("data")
                if isinstance(items, list) and items:
                    return parse_virtual(items[0])

            url = build_token_by_pretoken_url(token_address, chain=chain)
            data, error = await self._get_json(url)
            if error is not None or not isinstance(data, dict):
                return None
            items = data.get("data")
            if not isinstance(items, list) or not items:
                return None
            return parse_virtual(items[0])
        except Exception as exc:
            logger.info("virtuals: fetch_by_address echec inattendu — %s", exc)
            return None


virtuals_client = VirtualsClient()
