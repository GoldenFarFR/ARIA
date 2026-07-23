from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from urllib.parse import quote

from aria_core import outgoing_pause
from aria_core.exchanges import ExchangeStatus, create_exchange
from aria_core.identity import official_x_at, official_x_handle
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

X_API_BASE = "https://api.twitter.com/2"
def _curiosity_accounts() -> tuple[str, ...]:
    from aria_core.knowledge.x_watchlist import all_curiosity_handles

    return all_curiosity_handles()


def is_x_read_configured() -> bool:
    return bool(settings.x_bearer_token.strip())


def is_x_post_configured() -> bool:
    return all(
        (
            settings.x_api_key.strip(),
            settings.x_api_secret.strip(),
            settings.x_access_token.strip(),
            settings.x_access_token_secret.strip(),
        )
    )


def is_x_configured() -> bool:
    return is_x_read_configured() or is_x_post_configured()


def is_x_reading_active() -> bool:
    """True only when a real read actually happens: bearer configured AND at least
    one read-consuming gate is on (curiosity feed, auto-reply, mentions learn,
    diligence de conviction).

    Bearer presence alone is NOT enough — reading can be (and, per operator
    decision 11/07, currently is) deliberately cut for cost control while the
    bearer token stays configured. Never do a live API call here (cost/sobriety) —
    derive the real state from the same gates the heartbeat/engagement cycles use.
    """
    if not is_x_read_configured():
        return False
    return bool(
        getattr(settings, "x_curiosity_enabled", False)
        or settings.x_allow_replies
        or getattr(settings, "x_mentions_learn_enabled", False)
        or getattr(settings, "aria_conviction_research_enabled", False)
    )


def x_status() -> dict[str, Any]:
    return {
        "handle": official_x_at(),
        "read": is_x_read_configured(),
        "reading_active": is_x_reading_active(),
        "post": is_x_post_configured(),
        "configured": is_x_configured(),
        "curiosity_enabled": is_x_read_configured(),
        "api_mode": "pay_per_use",
        "free_fallback": "intent_url",
    }


def x_intent_post_url(text: str) -> str:
    """Free fallback — opens X compose UI (manual confirm, no API credits)."""
    return f"https://twitter.com/intent/tweet?text={quote(text.strip()[:280])}"


def _is_credits_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "402" in msg or "credits" in msg


def _oauth1_auth():
    from requests_oauthlib import OAuth1

    return OAuth1(
        settings.x_api_key.strip(),
        client_secret=settings.x_api_secret.strip(),
        resource_owner_key=settings.x_access_token.strip(),
        resource_owner_secret=settings.x_access_token_secret.strip(),
    )


def _upload_media_sync(image_path: Path) -> str:
    import requests

    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    auth = _oauth1_auth()
    with image_path.open("rb") as handle:
        response = requests.post(
            "https://upload.twitter.com/1.1/media/upload.json",
            auth=auth,
            files={"media": handle},
            timeout=120,
        )
    data = response.json() if response.content else {}
    if response.status_code not in (200, 201):
        err = data.get("errors") or data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X media upload {response.status_code}: {err}")
    media_id = data.get("media_id_string") or data.get("media_id")
    if not media_id:
        raise RuntimeError("X media upload: missing media_id")
    return str(media_id)


def _post_tweet_sync(
    text: str,
    *,
    media_ids: list[str] | None = None,
    in_reply_to_tweet_id: str | None = None,
) -> dict[str, Any]:
    import requests

    from aria_core.x_text import fit_x_tweet

    # Backstop kill-switch: no path (even future) publishes to X during a pause.
    if outgoing_pause.is_paused():
        raise RuntimeError(outgoing_pause.blocked_notice("La publication sur X"))
    body = fit_x_tweet(text.strip())
    if not body:
        raise ValueError("Empty tweet text")
    auth = _oauth1_auth()
    payload: dict[str, Any] = {"text": body}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}
    if in_reply_to_tweet_id:
        payload["reply"] = {"in_reply_to_tweet_id": str(in_reply_to_tweet_id).strip()}
    response = requests.post(
        f"{X_API_BASE}/tweets",
        auth=auth,
        json=payload,
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code == 429:
        from aria_core.x_publication_policy import record_x_rate_limit

        reset_raw = response.headers.get("x-rate-limit-reset")
        try:
            reset_ts = int(reset_raw) if reset_raw else None
        except (TypeError, ValueError):
            reset_ts = None
        record_x_rate_limit(reset_ts=reset_ts)
        err = data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X API 429 rate limit: {err}")
    if response.status_code not in (200, 201):
        err = data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X API {response.status_code}: {err}")
    return data


def _verify_me_sync() -> dict[str, Any]:
    import requests

    auth = _oauth1_auth()
    response = requests.get(
        f"{X_API_BASE}/users/me",
        auth=auth,
        params={"user.fields": "username,name"},
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code != 200:
        err = data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X verify {response.status_code}: {err}")
    return data.get("data", {})


def _update_profile_image_sync(image_path: Path) -> dict[str, Any]:
    import requests

    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    if image_path.stat().st_size > 700 * 1024:
        raise ValueError("X profile image must be <= 700 KB")

    auth = _oauth1_auth()
    with image_path.open("rb") as handle:
        response = requests.post(
            "https://api.twitter.com/1.1/account/update_profile_image.json",
            auth=auth,
            files={"image": handle},
            timeout=60,
        )
    data = response.json() if response.content else {}
    if response.status_code != 200:
        err = data.get("errors") or data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X profile image {response.status_code}: {err}")
    return data


async def apply_profile_image(image_path: Path) -> bool:
    """Sync @Aria_ZHC profile photo on X (OAuth 1.0a, Read+Write)."""
    if outgoing_pause.is_paused():
        logger.info("X profile image sync bloqué — ARIA en pause (%s)", outgoing_pause.since_label())
        return False
    if not is_x_post_configured():
        logger.info("X profile image skipped — post OAuth keys not configured")
        return False
    try:
        await asyncio.to_thread(_update_profile_image_sync, image_path)
        logger.info("X profile photo updated: %s", image_path.name)
        return True
    except Exception as exc:
        logger.warning("X profile image sync failed: %s", exc)
        return False


def _fetch_me_profile_sync(*, extra_fields: str = "") -> dict[str, Any]:
    import requests

    fields = "profile_banner_url,profile_image_url,name,description,url,location,username"
    if extra_fields:
        fields = extra_fields
    auth = _oauth1_auth()
    response = requests.get(
        f"{X_API_BASE}/users/me",
        auth=auth,
        params={"user.fields": fields},
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code != 200:
        err = data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X profile fetch {response.status_code}: {err}")
    return data.get("data", {})


def _update_profile_fields_sync(
    *,
    name: str | None = None,
    description: str | None = None,
    url: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    import requests

    payload: dict[str, str] = {}
    if name is not None:
        payload["name"] = name.strip()[:50]
    if description is not None:
        payload["description"] = description.strip()[:160]
    if url is not None:
        payload["url"] = url.strip()[:100]
    if location is not None:
        payload["location"] = location.strip()[:30]
    if not payload:
        raise ValueError("No profile fields to update")

    auth = _oauth1_auth()
    response = requests.post(
        "https://api.twitter.com/1.1/account/update_profile.json",
        auth=auth,
        data=payload,
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code != 200:
        err = data.get("errors") or data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X profile update {response.status_code}: {err}")
    return data


async def fetch_x_profile_fields() -> dict[str, str]:
    """Profile text fields for @Aria_ZHC (OAuth)."""
    if not is_x_post_configured():
        return {}
    me = await asyncio.to_thread(_fetch_me_profile_sync)
    return {
        "name": str(me.get("name") or ""),
        "description": str(me.get("description") or ""),
        "url": str(me.get("url") or ""),
        "location": str(me.get("location") or ""),
        "username": str(me.get("username") or ""),
    }


async def apply_x_profile_fields(profile: dict[str, str]) -> bool:
    """Applies name, bio, site, location on @Aria_ZHC."""
    if outgoing_pause.is_paused():
        logger.info("X profile text sync bloqué — ARIA en pause (%s)", outgoing_pause.since_label())
        return False
    if not is_x_post_configured():
        logger.info("X profile text skipped — post OAuth keys not configured")
        return False
    try:
        await asyncio.to_thread(
            _update_profile_fields_sync,
            name=profile.get("name"),
            description=profile.get("description"),
            url=profile.get("url"),
            location=profile.get("location"),
        )
        logger.info("X profile text updated for @%s", official_x_handle())
        return True
    except Exception as exc:
        logger.warning("X profile text sync failed: %s", exc)
        return False


def _update_profile_banner_sync(image_path: Path) -> dict[str, Any]:
    import requests

    if not image_path.is_file():
        raise FileNotFoundError(str(image_path))
    max_bytes = 3 * 1024 * 1024
    if image_path.stat().st_size > max_bytes:
        raise ValueError("X profile banner must be <= 3 MB")

    auth = _oauth1_auth()
    with image_path.open("rb") as handle:
        response = requests.post(
            "https://api.twitter.com/1.1/account/update_profile_banner.json",
            auth=auth,
            files={"banner": handle},
            timeout=90,
        )
    data = response.json() if response.content else {}
    if response.status_code not in (200, 201):
        err = data.get("errors") or data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X profile banner {response.status_code}: {err}")
    return data


async def get_profile_banner_status() -> dict[str, Any]:
    """Banner status for @Aria_ZHC (OAuth)."""
    if not is_x_post_configured():
        return {"has_banner": False, "banner_url": None, "username": None}
    try:
        me = await asyncio.to_thread(_fetch_me_profile_sync)
        url = me.get("profile_banner_url") or ""
        return {
            "has_banner": bool(url),
            "banner_url": url or None,
            "username": me.get("username"),
        }
    except Exception as exc:
        logger.warning("X banner status failed: %s", exc)
        return {"has_banner": False, "banner_url": None, "error": str(exc)[:200]}


async def apply_profile_banner(image_path: Path) -> bool:
    """Sync header banner for @Aria_ZHC (OAuth 1.0a, Read+Write)."""
    if outgoing_pause.is_paused():
        logger.info("X profile banner sync bloqué — ARIA en pause (%s)", outgoing_pause.since_label())
        return False
    if not is_x_post_configured():
        logger.info("X profile banner skipped — post OAuth keys not configured")
        return False
    try:
        await asyncio.to_thread(_update_profile_banner_sync, image_path)
        logger.info("X profile banner updated: %s", image_path.name)
        return True
    except Exception as exc:
        logger.warning("X profile banner sync failed: %s", exc)
        return False


async def verify_x_connection() -> tuple[bool, str]:
    if not is_x_post_configured():
        return False, "Ajoute X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET dans .env"
    try:
        me = await asyncio.to_thread(_verify_me_sync)
        username = me.get("username", "?")
        name = me.get("name", "?")
        expected = official_x_handle().lower()
        if username.lower() != expected:
            return True, (
                f"Connecté en @{username} ({name}) — attendu @{expected}. "
                "Vérifie les tokens OAuth du compte @Aria_ZHC."
            )
        return True, f"X connecté : @{username} ({name})"
    except Exception as exc:
        logger.warning("X verify failed: %s", exc)
        return False, str(exc)[:300]


def is_placeholder_x_insight(text: str, topic: str = "") -> bool:
    """Legacy mock curiosity item — never store as cognitive knowledge."""
    lower = (text or "").lower()
    if (topic or "").strip() == "x_setup":
        return True
    return "configure x_bearer_token" in lower and "oauth keys" in lower


async def fetch_curiosity_feed() -> list[dict]:
    if not is_x_read_configured():
        return []

    headers = {"Authorization": f"Bearer {settings.x_bearer_token.strip()}"}
    items: list[dict] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for account in _curiosity_accounts():
            try:
                user_res = await client.get(
                    f"{X_API_BASE}/users/by/username/{account}",
                    headers=headers,
                )
                if user_res.status_code != 200:
                    continue
                user_id = user_res.json().get("data", {}).get("id")
                if not user_id:
                    continue

                tweets_res = await client.get(
                    f"{X_API_BASE}/users/{user_id}/tweets",
                    headers=headers,
                    params={"max_results": 5, "tweet.fields": "created_at,public_metrics"},
                )
                if tweets_res.status_code != 200:
                    continue

                for tweet in tweets_res.json().get("data", []):
                    text = tweet.get("text", "")
                    if not text.strip() or is_placeholder_x_insight(text):
                        continue
                    items.append({
                        "topic": f"@{account}",
                        "text": text,
                        "confidence": 0.7,
                        "tweet_id": tweet.get("id"),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as exc:
                logger.warning("X fetch failed for %s: %s", account, exc)

    return items


async def search_recent_tweets(query: str, *, max_results: int = 10) -> list[dict]:
    """X search by free-form query (ticker, contract address, keyword) --
    ``GET /tweets/search/recent`` (X API v2, 7-day sliding window). Used by
    ``conviction_research.py`` (19/07, explicit operator request) to see recent
    buzz on a token before a purchase -- distinct from ``fetch_curiosity_feed`` (fixed
    accounts followed for ARIA's general curiosity, not a topic search).

    Standard dome: empty query/missing bearer -> empty list without a network call; any
    HTTP failure degrades to an empty list (never an exception surfaced to the caller, never
    a fabricated tweet)."""
    q = (query or "").strip()
    if not q or not is_x_read_configured():
        return []

    headers = {"Authorization": f"Bearer {settings.x_bearer_token.strip()}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(
                f"{X_API_BASE}/tweets/search/recent",
                headers=headers,
                params={
                    "query": q[:500],
                    "max_results": max(10, min(int(max_results), 100)),
                    "tweet.fields": "created_at,public_metrics,author_id",
                },
            )
            if res.status_code != 200:
                logger.warning("X search_recent_tweets HTTP %s pour %r", res.status_code, q)
                return []
            data = res.json().get("data", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("X search_recent_tweets échec pour %r: %s", q, exc)
        return []

    return [
        {
            "text": t.get("text", ""),
            "created_at": t.get("created_at"),
            "tweet_id": t.get("id"),
            "author_id": t.get("author_id"),
            "public_metrics": t.get("public_metrics", {}),
        }
        for t in data
        if (t.get("text") or "").strip()
    ]


async def fetch_user_recent_tweets(username: str, *, max_results: int = 10) -> list[dict]:
    """Recent timeline of an X account by its handle -- reused to evaluate a
    project's publication cadence (active vs. near-dead, 19/07) via
    ``conviction_research.py``. Same dome as ``search_recent_tweets``: degrades to an
    empty list on any failure, never an exception or fabricated data."""
    handle = (username or "").lstrip("@").strip()
    if not handle or not is_x_read_configured():
        return []

    headers = {"Authorization": f"Bearer {settings.x_bearer_token.strip()}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            user_res = await client.get(f"{X_API_BASE}/users/by/username/{handle}", headers=headers)
            if user_res.status_code != 200:
                return []
            user_id = user_res.json().get("data", {}).get("id")
            if not user_id:
                return []

            tweets_res = await client.get(
                f"{X_API_BASE}/users/{user_id}/tweets",
                headers=headers,
                params={"max_results": max(5, min(int(max_results), 100)), "tweet.fields": "created_at"},
            )
            if tweets_res.status_code != 200:
                return []
            data = tweets_res.json().get("data", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("X fetch_user_recent_tweets échec pour @%s: %s", handle, exc)
        return []

    return [
        {"text": t.get("text", ""), "created_at": t.get("created_at"), "tweet_id": t.get("id")}
        for t in data
        if (t.get("text") or "").strip()
    ]


def _coerce_media_paths(
    media_paths: list[Path] | Path | str | None,
) -> list[Path]:
    if media_paths is None:
        return []
    if isinstance(media_paths, (str, Path)):
        return [Path(media_paths)]
    return [Path(p) for p in media_paths]


async def post_tweet(
    text: str,
    approval_id: str = "operator",
    *,
    force: bool = False,
    skip_rate_gap: bool = False,
    media_paths: list[Path] | Path | str | None = None,
) -> tuple[Any, str]:
    """Post to @Aria_ZHC when OAuth user context keys are configured."""
    if outgoing_pause.is_paused():
        return None, outgoing_pause.blocked_notice("La publication d'un tweet")
    from aria_core.handle_registry import resolve_handles_in_text
    from aria_core.x_publication_policy import (
        check_tweet_allowed,
        policy_summary,
        record_tweet_posted,
    )

    from aria_core.x_text import fit_x_tweet

    text = fit_x_tweet(resolve_handles_in_text(text.strip()))
    allowed, reason, cost = check_tweet_allowed(
        text,
        force=force,
        skip_rate_gap=skip_rate_gap,
    )
    if not allowed:
        return None, (
            f"Publication bloquée par la politique X.\n{reason}\n\n"
            f"{policy_summary('fr')}\n\n"
            f"Brouillon :\n{text[:280]}\n\n"
            f"Lien 1-clic (gratuit) :\n{x_intent_post_url(text)}"
        )

    exchange = await create_exchange(
        target_agent="X_public",
        channel="x_api",
        message_body=text,
        message_json={"handle": official_x_at(), "approval_id": approval_id},
        approval_id=approval_id,
        status=ExchangeStatus.APPROVED,
    )

    if not is_x_post_configured():
        return exchange, (
            f"Tweet préparé #{exchange.id} — X pas connecté.\n\n"
            "Ajoute dans backend/.env :\n"
            "X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET\n"
            "(developer.x.com → app → OAuth 1.0a, droits Read+Write)\n\n"
            f"Texte :\n{text[:280]}\n\n"
            f"Puis : published {exchange.id}"
        )

    try:
        media_ids: list[str] = []
        for path in _coerce_media_paths(media_paths):
            media_ids.append(await asyncio.to_thread(_upload_media_sync, path))
        result = await asyncio.to_thread(
            _post_tweet_sync,
            text,
            media_ids=media_ids or None,
        )
        tweet_id = result.get("data", {}).get("id", "")
        url = f"https://x.com/{official_x_handle()}/status/{tweet_id}" if tweet_id else ""
        record_tweet_posted(text, tweet_id=tweet_id, cost_usd=cost)
        note = f"Publié sur X #{exchange.id} (~{cost:.3f} $)"
        if url:
            note += f"\n{url}"
        return exchange, note
    except Exception as exc:
        logger.error("X post failed: %s", exc)
        body = text[:280]
        if _is_credits_error(exc):
            intent = x_intent_post_url(body)
            return exchange, (
                f"API X sans crédits (pay-per-use ~0,015 $/tweet).\n\n"
                f"Tweet prêt #{exchange.id} :\n{body}\n\n"
                f"Publier gratuitement (1 clic, compte @Aria_ZHC) :\n{intent}\n\n"
                f"Ou recharge des crédits sur console.x.com puis /x post …"
            )
        return exchange, (
            f"Échec publication X #{exchange.id} : {exc}\n\n"
            f"Texte :\n{body}\n\n"
            f"Lien 1-clic :\n{x_intent_post_url(body)}"
        )


async def reply_to_tweet(
    text: str,
    *,
    in_reply_to_tweet_id: str,
    approval_id: str = "x_mention",
    force: bool = False,
) -> tuple[str | None, str]:
    """Reply on X thread when X_ALLOW_REPLIES=true (~0,015 $)."""
    if outgoing_pause.is_paused():
        return None, outgoing_pause.blocked_notice("La réponse sur X")
    from aria_core.handle_registry import resolve_handles_in_text
    from aria_core.x_publication_policy import (
        check_reply_allowed,
        record_engagement,
    )

    body = resolve_handles_in_text(text.strip())[:280]
    allowed, reason, cost = check_reply_allowed(body, force=force)
    if not allowed:
        return None, f"Reply bloquée : {reason}"

    if not is_x_post_configured():
        return None, "OAuth X requis pour répondre sur X"

    try:
        result = await asyncio.to_thread(
            _post_tweet_sync,
            body,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
        )
        reply_id = result.get("data", {}).get("id", "")
        record_engagement(
            "reply",
            target=f"{in_reply_to_tweet_id}:{reply_id}",
            cost_usd=cost,
        )
        url = (
            f"https://x.com/{official_x_handle()}/status/{reply_id}"
            if reply_id
            else ""
        )
        note = f"Reply publiée (~{cost:.3f} $)"
        if url:
            note += f"\n{url}"
        return reply_id or None, note
    except Exception as exc:
        logger.error("X reply failed: %s", exc)
        return None, f"Échec reply X : {exc}"