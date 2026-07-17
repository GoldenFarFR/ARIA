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
from urllib.parse import quote

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
WEB_BASE_URL = "https://dexscreener.com"

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
    price_change_h6: float = 0.0
    price_change_h1: float = 0.0
    price_change_m5: float = 0.0
    buys_24h: int = 0
    sells_24h: int = 0
    pair_created_at: int | None = None
    base_address: str = ""  # adresse du token de base (#194) -- pour corréler un lot
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
    change = raw.get("priceChange")
    change = change if isinstance(change, dict) else {}
    return PairSnapshot(
        pair_address=str(raw.get("pairAddress") or ""),
        dex_id=str(raw.get("dexId") or ""),
        liquidity_usd=float(liq.get("usd") or 0),
        volume_24h_usd=float(vol.get("h24") or 0),
        price_usd=float(raw.get("priceUsd") or 0),
        price_change_24h=float(change.get("h24") or 0),
        price_change_h6=float(change.get("h6") or 0),
        price_change_h1=float(change.get("h1") or 0),
        price_change_m5=float(change.get("m5") or 0),
        buys_24h=int(h24.get("buys") or 0) if isinstance(h24, dict) else 0,
        sells_24h=int(h24.get("sells") or 0) if isinstance(h24, dict) else 0,
        pair_created_at=int(raw.get("pairCreatedAt") or 0) or None,
        base_address=str(base.get("address") or "").lower(),
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


def token_url(contract: str, *, chain: str = "base") -> str:
    """URL DexScreener publique (page web, pas l'API) pour ``contract`` sur ``chain`` --
    17/07, demande opérateur : chaque position ARIA doit être reliée au vrai graphique.
    Construction pure (aucun appel réseau) : DexScreener utilise le même identifiant de
    chaîne dans ses URLs web que dans son API (``chain`` tel que déjà stocké sur une
    position), pas de table de correspondance à maintenir. Forme "adresse du token" (pas
    une paire précise) -- DexScreener choisit lui-même la paire la plus liquide à
    afficher, cohérent avec ``_best_pair`` côté scan (liquidité la plus haute)."""
    return f"{WEB_BASE_URL}/{(chain or 'base').strip().lower()}/{(contract or '').strip().lower()}"


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


async def search_pairs(query: str) -> list[PairSnapshot]:
    """Recherche libre DexScreener (``/latest/dex/search``, #194, 15/07) -- couvre
    TOUTES les chaînes indexées (pas un endpoint par chaîne), source de sourcing
    multi-chaînes vérifiée en direct (curl, HTTP 200) avant construction. Même
    forme de paire que ``token-pairs/v1`` (``_parse_pair`` réutilisé tel quel).
    Liste vide si aucun résultat OU si l'appel échoue -- jamais une exception."""
    url = f"{BASE_URL}/latest/dex/search?q={quote(query)}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: search '%s' -> %s", query[:30], error)
        return []
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        return []
    return [_parse_pair(row) for row in pairs if isinstance(row, dict)]


@dataclass
class TokenListing:
    """Entrée « boost » ou « profil » DexScreener (#194) -- métadonnées de
    découverte SANS donnée de prix/liquidité (contrairement à ``PairSnapshot``) :
    juste de quoi identifier un contrat + chaîne à passer ensuite au vrai pipeline
    de décision (honeypot + TA + R/R), jamais utilisé seul comme signal d'achat."""

    chain_id: str = ""
    token_address: str = ""
    description: str = ""
    links: list[dict] = field(default_factory=list)


def parse_listing(raw: dict) -> TokenListing:
    """Rendu public (#196, était ``_parse_listing``) : réutilisé tel quel par
    ``aria_core.momentum_websocket`` -- les frames WebSocket DexScreener (vérifié
    en direct 16/07) portent EXACTEMENT la même forme par élément que la réponse
    REST (``chainId``/``tokenAddress``/``description``/``links``), aucun parsing
    dupliqué."""
    links: list[dict] = []
    for link in raw.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        kind = str(link.get("type") or "").strip().lower()
        label = str(link.get("label") or "") or _SOCIAL_LABELS.get(kind, kind.capitalize() or "Lien")
        links.append({"label": label, "url": url})
    return TokenListing(
        chain_id=str(raw.get("chainId") or ""),
        token_address=str(raw.get("tokenAddress") or ""),
        description=str(raw.get("description") or ""),
        links=links,
    )


async def _fetch_listings(path: str) -> list[TokenListing]:
    data, error = await _get_json(f"{BASE_URL}{path}")
    if error is not None:
        logger.warning("dexscreener: %s -> %s", path, error)
        return []
    if not isinstance(data, list):
        return []
    return [parse_listing(row) for row in data if isinstance(row, dict)]


async def token_boosts_top() -> list[TokenListing]:
    """Tokens actuellement les plus « boostés » (promotion payante DexScreener,
    #194) -- signal « quelqu'un investit pour la visibilité de CE token
    maintenant », jamais un signal d'achat à lui seul (bonus de sourcing)."""
    return await _fetch_listings("/token-boosts/top/v1")


async def token_boosts_latest() -> list[TokenListing]:
    """Boosts les plus RÉCENTS (#194) -- favorise la fraîcheur (« signaux qui
    commencent à se former ») plutôt qu'un classement déjà bien avancé."""
    return await _fetch_listings("/token-boosts/latest/v1")


async def token_profiles_latest() -> list[TokenListing]:
    """Profils projet les plus récemment CRÉÉS (#194) -- sourcing de tokens frais
    avec metadata renseignée, indépendant des boosts payants."""
    return await _fetch_listings("/token-profiles/latest/v1")


async def token_profiles_recent_updates() -> list[TokenListing]:
    """Profils projet les plus récemment MIS À JOUR (#194, distinct de
    ``token_profiles_latest`` qui couvre les créations) -- capture un projet qui
    vient de retoucher ses métadonnées, signal d'activité récente."""
    return await _fetch_listings("/token-profiles/recent-updates/v1")


async def fetch_tokens_batch(addresses: list[str], *, chain: str = "base") -> list[PairSnapshot]:
    """``/tokens/v1/{chainId}/{tokenAddresses}`` (#194, spec OpenAPI officielle
    vérifiée -- docs/aria-learning-inbox/2026-07-15-dexscreener-openapi-spec-verifiee.yaml) :
    jusqu'à 30 adresses séparées par des virgules en UN SEUL appel (300 req/min),
    bien plus efficace que N appels ``token-pairs/v1`` individuels pour pré-filtrer
    un lot de candidats sourcés (liquidité) avant le pipeline de décision complet.
    Adresses au-delà de 30 silencieusement tronquées (limite documentée de l'API,
    jamais un appel qui échouerait silencieusement sur un lot trop grand)."""
    addrs = [a.strip() for a in addresses if a and a.strip()][:30]
    if not addrs:
        return []
    url = f"{BASE_URL}/tokens/v1/{chain}/{','.join(addrs)}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: tokens/v1 batch (%s, %d adresses) -> %s", chain, len(addrs), error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_pair(row) for row in data if isinstance(row, dict)]


@dataclass
class MetaTrend:
    """Narratif/méta tendance DexScreener (#194, ``/metas/*``) -- ex. « AI »,
    regroupe plusieurs tokens sous un thème. Signal de CONTEXTE (un narratif chaud
    peut porter plusieurs candidats à la fois), jamais un signal d'achat isolé."""

    slug: str = ""
    name: str = ""
    description: str = ""
    market_cap: float = 0.0
    liquidity: float = 0.0
    volume: float = 0.0
    token_count: int = 0
    market_cap_change_24h: float = 0.0


def _parse_meta(raw: dict) -> MetaTrend:
    change = raw.get("marketCapChange")
    change_24h = float(change.get("h24") or 0) if isinstance(change, dict) else 0.0
    return MetaTrend(
        slug=str(raw.get("slug") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        market_cap=float(raw.get("marketCap") or 0),
        liquidity=float(raw.get("liquidity") or 0),
        volume=float(raw.get("volume") or 0),
        token_count=int(raw.get("tokenCount") or 0),
        market_cap_change_24h=change_24h,
    )


async def metas_trending() -> list[MetaTrend]:
    """Narratifs tendance (#194, ``/metas/trending/v1``)."""
    data, error = await _get_json(f"{BASE_URL}/metas/trending/v1")
    if error is not None:
        logger.warning("dexscreener: metas/trending -> %s", error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_meta(row) for row in data if isinstance(row, dict)]


async def meta_by_slug(slug: str) -> tuple[MetaTrend | None, list[PairSnapshot]]:
    """Détail d'un narratif + ses paires (#194, ``/metas/meta/v1/{slug}``).
    ``(None, [])`` si indisponible -- jamais une paire inventée."""
    data, error = await _get_json(f"{BASE_URL}/metas/meta/v1/{quote(slug)}")
    if error is not None:
        logger.warning("dexscreener: metas/meta %s -> %s", slug, error)
        return None, []
    if not isinstance(data, dict):
        return None, []
    meta = _parse_meta(data)
    pairs_raw = data.get("pairs")
    pairs = (
        [_parse_pair(row) for row in pairs_raw if isinstance(row, dict)]
        if isinstance(pairs_raw, list)
        else []
    )
    return meta, pairs


# ---------------------------------------------------------------------------
# Synthèse dégradée de bougies (16/07, cascade OHLCV #194 -- demande opérateur
# explicite : "je veux que tous soit branchés meme si ils font la meme chose
# je veux une autoroute pas un departemental" / "cables les tous je veux une
# toile complete avec dexscreener et dune").
#
# DexScreener N'EXPOSE AUCUN endpoint OHLCV public (vérifié dans ce fichier --
# seulement des instantanés de paire + des fenêtres de variation agrégées
# m5/h1/h6/h24, jamais une vraie série de bougies). Ce n'est donc PAS un
# troisième fournisseur OHLCV au même titre que GeckoTerminal/CoinMarketCap --
# c'est une RECONSTRUCTION APPROXIMATIVE à partir de ce qui est déjà en main
# (``PairSnapshot`` déjà récupéré par ``evaluate_momentum_entry`` pour le prix
# courant, AUCUN appel réseau supplémentaire) : 5 points de prix (maintenant,
# -5m, -1h, -6h, -24h) dérivés à rebours du prix courant via les % de
# variation. Chaque "bougie" est un simple point OHLC dégénéré (open=high=
# low=close, volume=0) -- jamais un vrai chandelier avec mèches réelles.
#
# PORTÉE HONNÊTE : suffisant pour un biais de tendance grossier (EMA/MACD sur
# 5 points reste calculable mais peu significatif), quasiment inutile pour
# ``entry_signals.detect_entry`` (golden pocket + divergence RSI exige un
# vrai historique de prix, pas 5 points synthétiques) -- HOLD restera l'issue
# la plus probable même avec cette synthèse, ce qui est le comportement
# honnête attendu (jamais un R/R fabriqué sur une donnée aussi pauvre).
# Utilisé UNIQUEMENT en dernier recours après l'échec de GeckoTerminal ET
# CoinMarketCap -- gratuit et instantané, donc sans coût à essayer avant Dune
# (exécuteur SQL, lent, coûte des crédits).
def synthesize_candles_from_pair(pair: PairSnapshot) -> list[Candle]:
    """Reconstruction dégradée (voir commentaire ci-dessus) -- jamais un
    substitut d'un vrai OHLCV, seulement un dernier recours gratuit."""
    if not pair or not pair.price_usd or pair.price_usd <= 0:
        return []

    now_price = pair.price_usd
    windows = (
        ("h24", pair.price_change_24h),
        ("h6", pair.price_change_h6),
        ("h1", pair.price_change_h1),
        ("m5", pair.price_change_m5),
    )

    points: list[tuple[int, float]] = []
    for offset_seconds, (_label, pct_change) in zip((86_400, 21_600, 3_600, 300), windows):
        try:
            past_price = now_price / (1.0 + (pct_change / 100.0))
        except ZeroDivisionError:
            continue
        if past_price <= 0:
            continue
        points.append((-offset_seconds, past_price))

    points.append((0, now_price))
    points.sort(key=lambda p: p[0])

    return [
        Candle(ts=ts, open=price, high=price, low=price, close=price, volume=0.0)
        for ts, price in points
    ]
