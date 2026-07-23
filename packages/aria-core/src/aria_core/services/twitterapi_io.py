"""Client TwitterAPI.io -- profil X complet (followers/following/date de
création) à faible coût (0,18$/1000 profils, sourcé contre
``twitterapi.io/pricing``, WebFetch direct, 23/07), diligencé avant tout
branchement (ScamAdviser "legit and safe", Trustpilot positif, skill MCP
officielle packagée pour agents IA -- voir `docs/HANDOFF_MOTEUR_LEGITIMITE.md`).

Comble le vrai trou trouvé en construisant ``x_substance.py`` (23/07) : ni
twit.sh (métriques par tweet seulement) ni Tavily ``extract`` (rend la page
profil mais n'expose ni followers_count ni following_count, vérifié réel) ne
fournissaient de compteurs de compte -- seul le repli Tavily (âge du compte
via "Joined <mois année>") existait jusqu'ici.

``fetch_last_tweets`` (23/07, même session) ajoute activité/engagement --
demande opérateur explicite après un tableau comparatif confirmant que
twit.sh les fournit AUSSI, mais twit.sh est déjà utilisé par
``conviction_research.py`` (cadence de publication) : réutiliser twit.sh ICI
dupliquerait un appel payant sur le MÊME compte pour la MÊME fenêtre de
tweets récents, gaspillage du budget x402 PARTAGÉ (5$/semaine). TwitterAPI.io
a un endpoint dédié équivalent (``/twitter/user/last_tweets``, vérifié réel :
``createdAt`` + ``likeCount``/``replyCount``/``retweetCount``/``quoteCount``
par tweet) -- zéro nouveau fournisseur, zéro couplage avec
conviction_research.py (qui garde son propre chemin X officiel -> twit.sh,
inchangé).

Doctrine dôme standard (même patron que blockscout.py/goplus.py) : 429/5xx ->
1 retry après backoff court, puis dégradation (``None``, jamais une exception
qui remonte). Clé UNIQUEMENT depuis l'environnement (``TWITTERAPI_IO_KEY``),
jamais en dur, jamais loguée. Paiement prépayé côté fournisseur (crédits sur
leur dashboard, PAS x402) -- aucun budget dédié construit ici, l'opérateur
gère sa recharge comme pour GoPlus/Blockscout/CoinGecko.

Débit : sourcé sur le VRAI dashboard opérateur (23/07, capture réelle) --
palier "Free" = **0,2 QPS** (jamais payé) ou 3 QPS (ancien client, non
applicable ici). Calibré à 90% de 0,2 QPS -> intervalle minimum 5,5s (doctrine
CLAUDE.md "débit calibré à 90% de la capacité réelle, jamais devinée").
Attention -- ne pas confondre avec la doc générale (``docs.twitterapi.io/
introduction``), qui annonce "up to 200 QPS per client" : c'est la capacité
TECHNIQUE de l'infrastructure du fournisseur, pas le quota accordé à CE
compte selon son palier -- le dashboard réel du compte fait toujours
autorité sur la doc générale pour calibrer CE throttle.
Aucun coût de recharge à ce jour : 9964 crédits bonus offerts à l'inscription
(2 appels de test = 36 crédits consommés, soit 18 crédits/profil, cohérent
avec 0,18$/1000 -- 1$ = 100 000 crédits). Usage prévu de toute façon très
faible (1 appel par analyse VC, pas un flux continu)."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.twitterapi.io/twitter/user/info"
_LAST_TWEETS_URL = "https://api.twitterapi.io/twitter/user/last_tweets"
_TIMEOUT_SECONDS = 10.0
# 0,2 QPS réel (palier Free, dashboard opérateur) -> 5s/requête au maximum ;
# 90% de marge (doctrine CLAUDE.md) -> 5,5s.
_MIN_INTERVAL_SECONDS = 5.5

_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


@dataclass
class TwitterApiIoProfile:
    followers: int
    following: int
    created_at: datetime


@dataclass
class TwitterApiIoTweet:
    created_at: datetime
    like_count: int
    reply_count: int
    retweet_count: int
    quote_count: int


def is_twitterapi_io_configured() -> bool:
    return bool(os.environ.get("TWITTERAPI_IO_KEY", "").strip())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


def _parse_created_at(raw: object) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


async def fetch_user_profile(username: str) -> TwitterApiIoProfile | None:
    """Profil complet (followers/following/date de création) pour un handle X.
    ``None`` si la clé est absente, le compte introuvable, ou toute panne --
    jamais une exception qui remonte, jamais une donnée inventée."""
    handle = (username or "").lstrip("@").strip()
    if not handle:
        return None

    api_key = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(
                _API_URL,
                params={"userName": handle},
                headers={"X-API-Key": api_key},
            )
    except httpx.TransportError as exc:
        logger.info("twitterapi_io: panne réseau (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s pour @%s", r.status_code, handle)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- corps illisible, jamais une exception qui remonte
        return None

    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    created_at = _parse_created_at(data.get("createdAt"))
    followers = data.get("followers")
    following = data.get("following")
    if created_at is None or not isinstance(followers, int) or not isinstance(following, int):
        return None

    return TwitterApiIoProfile(followers=followers, following=following, created_at=created_at)


async def fetch_last_tweets(username: str, *, max_results: int = 20) -> list[TwitterApiIoTweet] | None:
    """Derniers tweets (date + engagement) pour un handle X -- utilisé pour
    l'activité/régularité et l'engagement du signal X Substance. ``None`` si
    la clé est absente ou toute panne ; jamais une exception qui remonte."""
    handle = (username or "").lstrip("@").strip()
    if not handle:
        return None

    api_key = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(
                _LAST_TWEETS_URL,
                params={"userName": handle},
                headers={"X-API-Key": api_key},
            )
    except httpx.TransportError as exc:
        logger.info("twitterapi_io: panne réseau last_tweets (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s pour last_tweets @%s", r.status_code, handle)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- corps illisible, jamais une exception qui remonte
        return None

    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    raw_tweets = payload.get("tweets")
    if not isinstance(raw_tweets, list):
        return None

    tweets: list[TwitterApiIoTweet] = []
    for item in raw_tweets[: max(1, min(int(max_results), 100))]:
        if not isinstance(item, dict):
            continue
        created_at = _parse_created_at(item.get("createdAt"))
        if created_at is None:
            continue
        tweets.append(
            TwitterApiIoTweet(
                created_at=created_at,
                like_count=int(item.get("likeCount") or 0),
                reply_count=int(item.get("replyCount") or 0),
                retweet_count=int(item.get("retweetCount") or 0),
                quote_count=int(item.get("quoteCount") or 0),
            )
        )
    return tweets or None
