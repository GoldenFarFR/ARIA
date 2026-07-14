"""Client DexScreener (lecture seule, public, sans clé) -- paires DEX (Base).

Extrait de ``skills/acp_onchain_scan.py`` (14/07, #157) pour être réutilisable
sans dupliquer un second client DexScreener : le wallet-scoring (#157) l'utilise
désormais aussi, en triangulation avec GeckoTerminal pour la résolution de pool
(``has_pool``) -- si GeckoTerminal ne trouve aucun pool pour un token mais que
DexScreener en trouve un, c'est un vrai signal (écart entre les deux sources),
pas juste un token illiquide. Comportement du scan `/vc` existant strictement
inchangé (même dataclass, même parsing, `acp_onchain_scan.py` délègue ici).

Ajout au passage (14/07) : retry sur 429/timeout, absent jusqu'ici (l'appel
d'origine ne retentait jamais un rate limit) -- même politique dôme que
``blockscout.py``/``geckoterminal.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"

_SOCIAL_LABELS = {
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "telegram": "Telegram",
    "discord": "Discord",
    "github": "GitHub",
    "reddit": "Reddit",
}


@dataclass
class PairSnapshot:
    pair_address: str = ""
    dex_id: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    price_usd: float = 0.0
    price_change_24h: float = 0.0
    buys_24h: int = 0
    sells_24h: int = 0
    pair_created_at: int | None = None
    base_symbol: str = ""
    quote_symbol: str = ""
    project_links: list[dict] = field(default_factory=list)


def _extract_project_links(raw: dict) -> list[dict]:
    """Liens officiels déclarés par le projet (DexScreener `info.websites`/`socials`).

    Aucune estimation : uniquement ce que DexScreener retourne réellement, et
    uniquement des URL http(s) (allowlist de schéma -- défense en profondeur,
    la donnée vient d'un tiers non fiable et sera de toute façon revalidée
    avant tout rendu HTML cliquable).
    """
    info = raw.get("info")
    if not isinstance(info, dict):
        return []

    links: list[dict] = []
    for site in info.get("websites") or []:
        if not isinstance(site, dict):
            continue
        url = str(site.get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            links.append({"label": str(site.get("label") or "Site officiel"), "url": url})

    for social in info.get("socials") or []:
        if not isinstance(social, dict):
            continue
        url = str(social.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        kind = str(social.get("type") or "").strip().lower()
        links.append({"label": _SOCIAL_LABELS.get(kind, kind.capitalize() or "Lien"), "url": url})

    return links


def _parse_pair(raw: dict) -> PairSnapshot:
    liq = raw.get("liquidity") or {}
    vol = raw.get("volume") or {}
    txns = raw.get("txns") or {}
    h24 = txns.get("h24") if isinstance(txns, dict) else {}
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    return PairSnapshot(
        pair_address=str(raw.get("pairAddress") or ""),
        dex_id=str(raw.get("dexId") or ""),
        liquidity_usd=float(liq.get("usd") or 0),
        volume_24h_usd=float(vol.get("h24") or 0),
        price_usd=float(raw.get("priceUsd") or 0),
        price_change_24h=float(raw.get("priceChange", {}).get("h24") or 0)
        if isinstance(raw.get("priceChange"), dict)
        else 0.0,
        buys_24h=int(h24.get("buys") or 0) if isinstance(h24, dict) else 0,
        sells_24h=int(h24.get("sells") or 0) if isinstance(h24, dict) else 0,
        pair_created_at=int(raw.get("pairCreatedAt") or 0) or None,
        base_symbol=str(base.get("symbol") or ""),
        quote_symbol=str(quote.get("symbol") or ""),
        project_links=_extract_project_links(raw),
    )


async def _get_json(url: str) -> tuple[object | None, str | None]:
    """GET avec retry sur 429/5xx/timeout -- même politique que blockscout.py/
    geckoterminal.py. L'implémentation d'origine (dans acp_onchain_scan.py)
    n'avait aucun retry ; un 429 isolé abandonnait net sans log."""
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexscreener: timeout sur %s -> %s", url, exc)
            return None, f"dexscreener indisponible (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("dexscreener: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, "dexscreener indisponible (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexscreener: HTTP %s sur %s", response.status_code, url)
            return None, f"dexscreener indisponible (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dexscreener: %s", exc)
            return None, f"dexscreener indisponible ({exc})"

        return response.json(), None


async def fetch_token_pairs(contract: str, *, chain: str = "base") -> list[PairSnapshot]:
    """Paires DEX connues pour ``contract`` sur ``chain``. Liste vide si aucune
    paire OU si l'appel échoue (jamais une exception qui remonte -- dégradation
    douce, même politique que le scan `/vc` existant)."""
    url = f"{BASE_URL}/token-pairs/v1/{chain}/{contract}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: token-pairs %s -> %s", contract[:10], error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_pair(row) for row in data if isinstance(row, dict)]


async def has_any_pair(contract: str, *, chain: str = "base") -> bool | None:
    """Triangulation (#157, 14/07) : ``True``/``False`` si DexScreener a répondu
    normalement (au moins une paire trouvée ou non), ``None`` si l'appel a
    échoué -- jamais confondre "aucune paire" avec "on n'a pas pu vérifier"."""
    url = f"{BASE_URL}/token-pairs/v1/{chain}/{contract}"
    data, error = await _get_json(url)
    if error is not None:
        return None
    if not isinstance(data, list):
        return None
    return len(data) > 0
