"""Deep read of ONE real web page -- direct reading capability for ARIA
when current search (DuckDuckGo/Tavily, short snippets) finds nothing
(task 13/07, real case: withluma.app, ARIA honestly said "I don't know"
for lack of a way to read the page itself).

Distinct from ``services/site_snapshot.py`` (pre-investment VC diligence,
capped at 600 characters, wired exclusively into the ``vc_analysis.py`` dome)
-- deliberately a separate file to never change that existing behavior.
A much more generous text budget here (reading, not a preview).

Same foundations as ``site_snapshot.py`` (httpx, regex-based HTML->text
extraction, no new parsing dependency) + an additional SSRF guard:
this capability is triggered by a URL PASTED BY THE OPERATOR in chat (cf.
``knowledge/web_verify.py::fetch_page_content``), i.e. a new "fetch an
arbitrary URL" surface exposed from user input -- defense in depth remains
justified even though the trigger is admin-only.

Read-only (GET), plain HTTP only in this version (no Playwright fallback
-- deferred to a v2, cf. 13/07 plan). A block (403, anti-bot,
timeout) fails honestly (``available=False``, explicit reason) rather than
fabricating replacement content.

**TOCTOU / DNS rebinding fix (pre-merge review, 13/07)**: an SSRF guard
that resolves then checks the host, but then lets the HTTP client redo
its OWN independent DNS resolution at the time of the actual connection,
is bypassable -- an attacker controlling the target domain's DNS can
return a legitimate public IP on the first lookup (the one checked by
``_resolve_and_guard``) then a private IP on the second (the one the
HTTP client would use). ``_resolve_and_guard`` therefore now returns the
VERIFIED IP itself, and ``fetch_page_text`` forces httpx to connect to it
directly via a custom ``httpcore.AsyncNetworkBackend``
(``_PinnedIPNetworkBackend``) -- no second DNS resolution happens. The
original hostname is still used for the HTTP Host and TLS SNI/certificate
validation (passed separately by httpcore, never derived from the IP):
the certificate is therefore correctly verified against the real domain.

**Residual limitation, deliberately accepted and documented rather than
hidden**: the pinned transport forces EVERY connection of this client to the
validated IP, including if the site responds with a redirect to ANOTHER
domain (``follow_redirects=True``). A redirect to the same host works
normally. A cross-domain redirect fails cleanly (the Host's domain name no
longer matches the pinned IP's certificate -> TLS/connection error,
never a silent connection to a third party) rather than being followed --
this is a safe failure by construction (the socket never leaves the
already-validated IP), not a workaround, but a legitimate redirect to
another domain will not be followed in this version.
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
_MAX_RAW_HTML_CHARS = 200_000  # bounds the parsing work, not the network size
_MAX_PAGE_TEXT_CHARS = 6000  # "deep" reading, not a preview (cf. site_snapshot.py: 600)
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
    """Real content of a page, never invented text."""

    url: str
    title: str = ""
    text: str = ""
    available: bool = False
    error: str | None = None


def _clean_text(raw: str) -> str:
    return _WS_RE.sub(" ", raw).strip()


def _extract_page_text(html: str) -> tuple[str, str]:
    """Returns ``(title, text)``. Same regex approach as ``site_snapshot.py``
    (no new HTML parsing dependency) -- title + meta-description +
    visible text, cleaned, truncated to ``_MAX_PAGE_TEXT_CHARS``."""
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
        return True  # unparseable IP -- err on the side of caution, refuse
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve_and_guard(hostname: str) -> tuple[str | None, str | None]:
    """Resolves ``hostname`` ONCE and refuses any private/loopback/
    link-local/reserved IP (SSRF guard). Returns ``(ip, None)`` if safe -- this IP
    is the one to PIN for the actual connection (cf. _PinnedIPNetworkBackend):
    letting the HTTP client redo its own independent resolution at the time
    of the connection would be a TOCTOU (DNS rebinding) that would bypass this
    guard entirely. Returns ``(None, error)`` if blocked. Resolution runs in a
    thread so it never freezes the async loop."""
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
    """Forces EVERY TCP connection of this transport to ``pinned_ip`` -- ignores
    the hostname httpcore would normally pass it for connect_tcp (which would
    trigger a DNS resolution independent from the one already checked by
    _resolve_and_guard). TLS/SNI (server_hostname) is handled separately by
    httpcore during start_tls, always with the real domain name -- never
    derived from the IP -- so certificate validation stays correct."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        self._real_backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await self._real_backend.connect_tcp(
            self._pinned_ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: nocover -- never used here
        return await self._real_backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._real_backend.sleep(seconds)


def _build_pinned_transport(pinned_ip: str) -> httpx.AsyncHTTPTransport:
    """httpx transport where ALL connections go through the already-validated IP
    -- no second independent DNS resolution takes place (cf. module docstring
    and _PinnedIPNetworkBackend)."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        network_backend=_PinnedIPNetworkBackend(pinned_ip),
    )
    return transport


async def fetch_page_text(url: str) -> PageFetchResult:
    """Real text content of ONE page -- plain HTTP only (no Playwright fallback
    in this version, cf. 13/07 plan). Never raises; network/SSRF/block
    failure -> ``available=False`` with an explicit reason,
    never invented replacement content."""
    if not url or not isinstance(url, str):
        return PageFetchResult(url=str(url or ""), available=False, error="URL absente")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return PageFetchResult(url=url, available=False, error="URL invalide (http/https uniquement)")

    pinned_ip, guard_error = await _resolve_and_guard(parsed.hostname)
    if guard_error:
        logger.info("page_reader: %s refused (%s)", parsed.hostname, guard_error)
        return PageFetchResult(url=url, available=False, error=guard_error)

    try:
        transport = _build_pinned_transport(pinned_ip)
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True, transport=transport,
        ) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except Exception as exc:  # noqa: BLE001 -- never blocking, honest failure below
        logger.info("page_reader: fetch %s failed (%s)", url, exc)
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
