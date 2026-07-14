"""Lecture profonde d'UNE page web réelle -- capacité de lecture directe pour ARIA
quand la recherche courante (DuckDuckGo/Tavily, extraits courts) ne trouve rien
(tâche 13/07, cas réel : withluma.app, ARIA a honnêtement dit "je ne sais pas"
faute de moyen de lire la page elle-même).

Distinct de ``services/site_snapshot.py`` (diligence VC pré-investissement,
plafonné à 600 caractères, câblé exclusivement dans le dôme ``vc_analysis.py``)
-- volontairement un fichier séparé pour ne jamais changer ce comportement
existant. Budget de texte beaucoup plus généreux ici (lecture, pas un aperçu).

Mêmes fondations que ``site_snapshot.py`` (httpx, extraction HTML->texte par
regex, pas de nouvelle dépendance de parsing) + garde SSRF supplémentaire :
cette capacité est déclenchée par une URL COLLÉE PAR L'OPÉRATEUR en chat (cf.
``knowledge/web_verify.py::fetch_page_content``), donc une nouvelle surface
"fetch une URL arbitraire" exposée depuis une entrée utilisateur -- la défense
en profondeur reste justifiée même si le déclencheur est admin-only.

Lecture seule (GET), HTTP simple uniquement dans cette version (pas de repli
Playwright -- reporté à une v2, cf. plan 13/07). Un blocage (403, anti-bot,
timeout) échoue honnêtement (``available=False``, raison explicite) plutôt que
de fabriquer un contenu de remplacement.

**Correctif TOCTOU / DNS rebinding (relecture pré-merge, 13/07)** : une garde
SSRF qui résout puis vérifie l'hôte, mais laisse ensuite le client HTTP
refaire sa PROPRE résolution DNS indépendante au moment de la connexion
réelle, est contournable -- un attaquant contrôlant le DNS du domaine cible
peut renvoyer une IP publique légitime au premier lookup (celui vérifié par
``_resolve_and_guard``) puis une IP privée au second (celui que le client
HTTP utiliserait). ``_resolve_and_guard`` renvoie donc désormais l'IP
VÉRIFIÉE elle-même, et ``fetch_page_text`` force httpx à s'y connecter
directement via un ``httpcore.AsyncNetworkBackend`` custom
(``_PinnedIPNetworkBackend``) -- aucune seconde résolution DNS n'a lieu. Le
nom d'hôte d'origine reste utilisé pour le Host HTTP et le SNI/la validation
de certificat TLS (passés séparément par httpcore, jamais dérivés de l'IP) :
le certificat est donc bien vérifié contre le vrai domaine.

**Limite résiduelle assumée, documentée plutôt que cachée** : le transport
épinglé force TOUTE connexion de ce client vers l'IP validée, y compris si le
site répond par une redirection vers un AUTRE domaine (``follow_redirects=
True``). Une redirection vers le même hôte fonctionne normalement. Une
redirection cross-domaine échoue proprement (nom de domaine du Host ne
correspond plus au certificat de l'IP épinglée -> erreur TLS/connexion,
jamais une connexion silencieuse vers un tiers) plutôt que d'être suivie --
c'est un échec sûr par construction (le socket ne quitte jamais l'IP déjà
validée), pas un contournement, mais une redirection légitime vers un autre
domaine ne sera pas suivie dans cette version.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpcore
import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 12.0
_MAX_RAW_HTML_CHARS = 200_000  # borne le travail de parsing, pas la taille réseau
_MAX_PAGE_TEXT_CHARS = 6000  # lecture "en profondeur", pas un aperçu (cf. site_snapshot.py: 600)
_USER_AGENT = "Mozilla/5.0 (compatible; AriaVanguardBot/1.0; +https://ariavanguardzhc.com)"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class PageFetchResult:
    """Contenu réel d'une page, jamais un texte inventé."""

    url: str
    title: str = ""
    text: str = ""
    available: bool = False
    error: str | None = None


def _clean_text(raw: str) -> str:
    return _WS_RE.sub(" ", raw).strip()


def _extract_page_text(html: str) -> tuple[str, str]:
    """Renvoie ``(title, text)``. Même approche regex que ``site_snapshot.py``
    (pas de nouvelle dépendance de parsing HTML) -- titre + meta-description +
    texte visible, nettoyé, tronqué à ``_MAX_PAGE_TEXT_CHARS``."""
    title_match = _TITLE_RE.search(html)
    title = _clean_text(title_match.group(1)) if title_match else ""
    desc_match = _META_DESC_RE.search(html)
    description = _clean_text(desc_match.group(1)) if desc_match else ""

    body = _SCRIPT_STYLE_RE.sub(" ", html)
    body = _TAG_RE.sub(" ", body)
    visible_text = _clean_text(body)

    parts = [p for p in (description, visible_text) if p]
    combined = " — ".join(parts)
    return title, combined[:_MAX_PAGE_TEXT_CHARS]


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # IP illisible -- prudence, on refuse
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve_and_guard(hostname: str) -> tuple[str | None, str | None]:
    """Résout ``hostname`` UNE SEULE FOIS et refuse toute IP privée/loopback/
    lien-local/réservée (garde SSRF). Renvoie ``(ip, None)`` si sûr -- cette IP
    est celle à ÉPINGLER pour la connexion réelle (cf. _PinnedIPNetworkBackend) :
    laisser le client HTTP refaire sa propre résolution indépendante au moment
    de la connexion serait un TOCTOU (DNS rebinding) qui contournerait cette
    garde entièrement. Renvoie ``(None, erreur)`` si bloqué. Résolution en
    thread pour ne jamais geler la boucle async."""
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        return None, f"hôte introuvable ({exc})"

    ips = [info[4][0] for info in addr_info]
    if not ips:
        return None, "hôte introuvable"
    for ip in ips:
        if _is_blocked_ip(ip):
            return None, "cible réseau interne/privée refusée"
    return ips[0], None


class _PinnedIPNetworkBackend(httpcore.AsyncNetworkBackend):
    """Force TOUTE connexion TCP de ce transport vers ``pinned_ip`` -- ignore le
    nom d'hôte que httpcore lui passerait normalement pour connect_tcp (qui
    déclencherait une résolution DNS indépendante de celle déjà vérifiée par
    _resolve_and_guard). Le TLS/SNI (server_hostname) est géré séparément par
    httpcore lors de start_tls, toujours avec le vrai nom de domaine -- jamais
    dérivé de l'IP -- donc la validation de certificat reste correcte."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        self._real_backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await self._real_backend.connect_tcp(
            self._pinned_ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: nocover -- jamais utilisé ici
        return await self._real_backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._real_backend.sleep(seconds)


def _build_pinned_transport(pinned_ip: str) -> httpx.AsyncHTTPTransport:
    """Transport httpx dont TOUTES les connexions passent par l'IP déjà validée
    -- aucune seconde résolution DNS indépendante n'a lieu (cf. docstring module
    et _PinnedIPNetworkBackend)."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        network_backend=_PinnedIPNetworkBackend(pinned_ip),
    )
    return transport


async def fetch_page_text(url: str) -> PageFetchResult:
    """Contenu texte réel d'UNE page -- HTTP simple uniquement (pas de repli
    Playwright dans cette version, cf. plan 13/07). Ne lève jamais ; échec
    réseau/SSRF/blocage -> ``available=False`` avec une raison explicite,
    jamais un contenu de remplacement inventé."""
    if not url or not isinstance(url, str):
        return PageFetchResult(url=str(url or ""), available=False, error="URL absente")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return PageFetchResult(url=url, available=False, error="URL invalide (http/https uniquement)")

    pinned_ip, guard_error = await _resolve_and_guard(parsed.hostname)
    if guard_error:
        logger.info("page_reader: %s refusé (%s)", parsed.hostname, guard_error)
        return PageFetchResult(url=url, available=False, error=guard_error)

    try:
        transport = _build_pinned_transport(pinned_ip)
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True, transport=transport,
        ) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant, échec honnête plus bas
        logger.info("page_reader: fetch %s échoué (%s)", url, exc)
        return PageFetchResult(url=url, available=False, error=f"page inaccessible ({type(exc).__name__})")

    if r.status_code == 403:
        return PageFetchResult(url=url, available=False, error="accès refusé par le site (403, probable anti-bot)")
    if r.status_code != 200:
        return PageFetchResult(url=url, available=False, error=f"page indisponible (HTTP {r.status_code})")

    content_type = r.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return PageFetchResult(url=url, available=False, error="contenu non-HTML, non lisible")

    title, text = _extract_page_text(r.text[:_MAX_RAW_HTML_CHARS])
    if not text:
        return PageFetchResult(url=url, available=False, error="page vide une fois nettoyée")

    return PageFetchResult(url=url, title=title, text=text, available=True)
