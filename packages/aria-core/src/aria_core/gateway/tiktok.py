"""Client TikTok (Content Posting API) -- seam publisher pour `release_pipeline.publish_release`.

Contexte (#34) : X publie déjà (`gateway/x_twitter.py`, actif, gaté `arm_campaign`).
TikTok reste un SEAM VIDE documenté depuis le 07/07 (`release_pipeline.publish_release`
accepte déjà un `tiktok_publisher` injectable -- best-effort, un canal qui échoue n'annule
jamais les autres -- mais aucun client réel n'existait derrière). Ce module pose ce
client, à minima : structure d'auth + upload + statut, gate OFF par défaut, aucun compte
TikTok requis pour le livrer (aucun appel réseau possible sans credentials réels).

Doctrine « dôme » (identique à tavily.py/goplus.py) : throttle, backoff exponentiel sur
429/5xx, 1 retry sur timeout puis dégradation explicite -- jamais un succès inventé.
Credentials UNIQUEMENT depuis l'environnement (jamais en dur, jamais logués), même
patron que `tavily_api_key()`.

Pourquoi l'adaptateur `tiktok_release_publisher` reste inerte même une fois le gate
armé : TikTok exige une VRAIE vidéo (`source_info.source=FILE_UPLOAD`) -- il n'existe
aujourd'hui aucun pipeline de génération vidéo côté ARIA (seam distinct, non construit,
cf. CLAUDE.md "TikTok = seam vide"). Poser un faux succès ici irait contre le principe
"jamais une donnée/un résultat inventé" -- l'adaptateur reste donc un point d'ancrage
honnête, prêt à appeler `TikTokClient.publish_video` le jour où un asset vidéo existe.

Avant publication réelle -- contrainte TikTok, pas un choix ARIA : tant que l'app n'a
pas passé l'audit officiel (developers.tiktok.com), toute vidéo Direct Post doit rester
`privacy_level=SELF_ONLY` (visible du seul compte). Ne JAMAIS élargir sans confirmation
explicite que l'audit est passé.

Doc officielle : https://developers.tiktok.com/doc/content-posting-api-get-started
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://open.tiktokapis.com"
TOKEN_URL = f"{API_BASE}/v2/oauth/token/"
VIDEO_INIT_URL = f"{API_BASE}/v2/post/publish/video/init/"
STATUS_URL = f"{API_BASE}/v2/post/publish/status/fetch/"

UNAVAILABLE = "TikTok indisponible"
_FAIL_STREAK_WARN_THRESHOLD = 3
_MAX_VIDEO_BYTES = 1_000_000_000  # limite officielle TikTok (1 Go)
_TRUTHY = ("1", "true", "yes", "on")


def tiktok_client_key() -> str:
    return os.environ.get("TIKTOK_CLIENT_KEY", "").strip()


def tiktok_client_secret() -> str:
    return os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()


def tiktok_refresh_token() -> str:
    return os.environ.get("TIKTOK_REFRESH_TOKEN", "").strip()


def is_tiktok_configured() -> bool:
    """Vrais identifiants présents -- aucun compte TikTok créé à ce jour (#34)."""
    return bool(tiktok_client_key() and tiktok_client_secret() and tiktok_refresh_token())


def is_tiktok_publish_enabled() -> bool:
    """Gate opérateur explicite, OFF par défaut. Credentials seuls ne suffisent jamais à
    publier (même doctrine que `is_x_reading_active` pour X : présence != autorisation)."""
    gate = os.environ.get("ARIA_TIKTOK_PUBLISH_ENABLED", "").strip().lower() in _TRUTHY
    return gate and is_tiktok_configured()


@dataclass
class TikTokPublishResult:
    """Résultat d'une tentative de publication. `published=False` + `error` si
    indisponible ; jamais un succès inventé."""

    published: bool
    publish_id: str | None = None
    error: str | None = None


class TikTokClient:
    """Client HTTP async, lecture/écriture, doctrine dôme (throttle + backoff)."""

    def __init__(self, *, min_interval: float = 1.0) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        self._access_token = ""

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
                "tiktok: %s echecs consecutifs (dernier: %s) — pas de blocage",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "tiktok: echec appel (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _post_json(
        self, url: str, payload: dict, *, headers: dict
    ) -> tuple[dict[str, Any] | None, str | None]:
        """POST avec la politique d'erreurs du dôme (identique à tavily._post_json).

        NB : le token vit dans le header Authorization -- jamais logué (on ne journalise
        que l'URL et le code d'erreur, jamais le payload ni les headers)."""
        attempt_429 = 0
        retried = False
        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
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
                self._record_failure(f"{url} -> HTTP {response.status_code} (token ?)")
                return None, f"{UNAVAILABLE} (token refusé ou expiré)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> HTTP {exc.response.status_code}")
                return None, f"{UNAVAILABLE} (HTTP {exc.response.status_code})"

            self._record_success()
            return response.json(), None

    async def refresh_access_token(self) -> bool:
        """Echange le refresh_token contre un access_token frais (expire ~24h, cf. doc
        officielle) -- OAuth2 x-www-form-urlencoded, pas JSON (contrainte TikTok)."""
        if not is_tiktok_configured():
            return False
        payload = {
            "client_key": tiktok_client_key(),
            "client_secret": tiktok_client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": tiktok_refresh_token(),
        }
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    TOKEN_URL,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TransportError as exc:
            self._record_failure(f"{TOKEN_URL} -> {exc}")
            return False
        if response.status_code != 200:
            self._record_failure(f"{TOKEN_URL} -> HTTP {response.status_code} (refresh refusé)")
            return False
        data = response.json()
        token = str(data.get("access_token") or "").strip()
        if not token:
            self._record_failure(f"{TOKEN_URL} -> reponse sans access_token")
            return False
        self._access_token = token
        self._record_success()
        return True

    async def _ensure_access_token(self) -> bool:
        if self._access_token:
            return True
        return await self.refresh_access_token()

    async def publish_video(
        self,
        video_path: Path,
        *,
        caption: str,
        privacy_level: str = "SELF_ONLY",
    ) -> TikTokPublishResult:
        """Publie une vidéo (Direct Post, source FILE_UPLOAD, chunk unique). Best-effort,
        jamais bloquant pour l'appelant.

        `privacy_level="SELF_ONLY"` par défaut : contrainte TikTok tant que l'app n'a pas
        passé l'audit officiel -- ne jamais élargir sans confirmation que l'audit est passé.
        """
        if not is_tiktok_publish_enabled():
            return TikTokPublishResult(
                published=False, error="TikTok publish désactivé (gate off ou non configuré)"
            )
        if not video_path.is_file():
            return TikTokPublishResult(published=False, error=f"fichier vidéo introuvable : {video_path}")
        video_size = video_path.stat().st_size
        if video_size <= 0 or video_size > _MAX_VIDEO_BYTES:
            return TikTokPublishResult(published=False, error=f"taille vidéo invalide ({video_size} octets)")

        if not await self._ensure_access_token():
            return TikTokPublishResult(published=False, error=f"{UNAVAILABLE} (auth)")

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        init_payload = {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        }
        data, error = await self._post_json(VIDEO_INIT_URL, init_payload, headers=headers)
        if error is not None or not isinstance(data, dict):
            return TikTokPublishResult(published=False, error=error or UNAVAILABLE)

        payload_data = data.get("data") or {}
        publish_id = str(payload_data.get("publish_id") or "").strip()
        upload_url = str(payload_data.get("upload_url") or "").strip()
        if not publish_id or not upload_url:
            self._record_failure(f"{VIDEO_INIT_URL} -> reponse sans publish_id/upload_url")
            return TikTokPublishResult(published=False, error=f"{UNAVAILABLE} (init incomplète)")

        uploaded = await self._upload_video_bytes(upload_url, video_path, video_size)
        if not uploaded:
            return TikTokPublishResult(published=False, publish_id=publish_id, error=f"{UNAVAILABLE} (upload)")

        return TikTokPublishResult(published=True, publish_id=publish_id)

    async def _upload_video_bytes(self, upload_url: str, video_path: Path, video_size: int) -> bool:
        """PUT du fichier complet en un seul chunk (cf. doc officielle Content-Range)."""
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.put(
                    upload_url,
                    content=video_path.read_bytes(),
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
                    },
                )
        except httpx.TransportError as exc:
            self._record_failure(f"{upload_url} -> {exc}")
            return False
        if response.status_code not in (200, 201):
            self._record_failure(f"{upload_url} -> HTTP {response.status_code}")
            return False
        self._record_success()
        return True

    async def fetch_publish_status(self, publish_id: str) -> str | None:
        """Interroge le statut d'une publication en cours. Best-effort, None si
        indisponible -- jamais un statut inventé."""
        if not await self._ensure_access_token():
            return None
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        data, error = await self._post_json(STATUS_URL, {"publish_id": publish_id}, headers=headers)
        if error is not None or not isinstance(data, dict):
            return None
        status = (data.get("data") or {}).get("status")
        return str(status).strip() or None


tiktok_client = TikTokClient()


async def tiktok_release_publisher(text: str, release: Any) -> bool:  # noqa: ARG001 -- signature imposée par release_pipeline.publish_release
    """Adaptateur `tiktok_publisher` injectable dans `release_pipeline.publish_release`.

    Reste honnêtement inerte (False -> `release_pipeline` classe le canal 'pending',
    comportement IDENTIQUE à aujourd'hui où aucun publisher n'est passé) : TikTok exige
    une vraie vidéo et aucun pipeline de génération vidéo n'existe côté ARIA -- ce n'est
    donc jamais un faux succès, juste un point d'ancrage prêt pour le jour où cet asset
    existera (appellera alors `tiktok_client.publish_video`)."""
    if not is_tiktok_publish_enabled():
        return False
    logger.info("tiktok_release_publisher: gate actif mais aucun pipeline vidéo -- reste pending")
    return False
