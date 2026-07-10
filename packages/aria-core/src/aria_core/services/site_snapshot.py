"""Instantané texte d'un site projet -- diligence produit pré-investissement.

Complète `project_activity.py` (GitHub) et `x_social.py` (réseaux) : ce que le
projet dit lui-même sur SON site. Lecture seule, best-effort, jamais bloquant.

Important -- ce contenu est déclaratif/marketing (le projet parle de lui-même),
jamais une vérification indépendante. Le texte extrait est destiné à finir dans
le bloc ``<donnees_non_fiables>`` du prompt LLM (`vc_analysis.py`), qui porte
déjà la garde anti-injection générique -- ce module ne fait QUE l'extraction,
aucune confiance particulière n'est accordée au contenu ici.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8.0
_MAX_RAW_HTML_CHARS = 40_000  # borne le travail de parsing, pas la taille réseau
_MAX_SNAPSHOT_CHARS = 600
_USER_AGENT = "Mozilla/5.0 (compatible; AriaVanguardBot/1.0; +https://ariavanguardzhc.com)"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str) -> str:
    return _WS_RE.sub(" ", raw).strip()


def _extract_snapshot_text(html: str) -> str:
    title_match = _TITLE_RE.search(html)
    title = _clean_text(title_match.group(1)) if title_match else ""
    desc_match = _META_DESC_RE.search(html)
    description = _clean_text(desc_match.group(1)) if desc_match else ""

    body = _SCRIPT_STYLE_RE.sub(" ", html)
    body = _TAG_RE.sub(" ", body)
    visible_text = _clean_text(body)

    parts = [p for p in (title, description, visible_text) if p]
    combined = " — ".join(parts)
    return combined[:_MAX_SNAPSHOT_CHARS]


async def fetch_site_text_snapshot(url: str | None) -> str | None:
    """Titre + meta-description + début du texte visible d'une page (tronqué,
    best-effort). None si l'URL est absente, la page indisponible, ou le
    contenu n'est pas du HTML."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
    except Exception as exc:  # noqa: BLE001 -- jamais bloquant
        logger.info("site_snapshot: fetch %s échoué (%s)", url, exc)
        return None

    if r.status_code != 200:
        return None
    content_type = r.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return None

    text = _extract_snapshot_text(r.text[:_MAX_RAW_HTML_CHARS])
    return text or None
