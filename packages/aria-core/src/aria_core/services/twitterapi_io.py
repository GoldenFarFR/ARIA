"""TwitterAPI.io client -- full X profile (followers/following/creation
date) at low cost ($0.18/1000 profiles, sourced against
``twitterapi.io/pricing``, direct WebFetch, 07/23), vetted before any
integration (ScamAdviser "legit and safe", positive Trustpilot, official
MCP skill packaged for AI agents -- see `docs/HANDOFF_MOTEUR_LEGITIMITE.md`).

Fills the real gap found while building ``x_substance.py`` (07/23): neither
twit.sh (per-tweet metrics only) nor Tavily ``extract`` (renders the profile
page but exposes neither followers_count nor following_count, verified for
real) provided account counters -- only the Tavily fallback (account age
via "Joined <month year>") existed until now.

``fetch_last_tweets`` (07/23, same session) adds activity/engagement --
explicit operator request after a comparison table confirmed that
twit.sh ALSO provides them, but twit.sh is already used by
``conviction_research.py`` (publishing cadence): reusing twit.sh HERE
would duplicate a paid call on the SAME account for the SAME window of
recent tweets, wasting the SHARED x402 budget ($5/week). TwitterAPI.io
has an equivalent dedicated endpoint (``/twitter/user/last_tweets``, verified
for real: ``createdAt`` + ``likeCount``/``replyCount``/``retweetCount``/
``quoteCount`` per tweet) -- zero new provider, zero coupling with
conviction_research.py (which keeps its own official X path -> twit.sh,
unchanged).

Standard dome doctrine (same pattern as blockscout.py/goplus.py): 429/5xx ->
1 retry after a short backoff, then degrade (``None``, never a bubbling
exception). Key ONLY from the environment (``TWITTERAPI_IO_KEY``), never
hardcoded, never logged. Payment prepaid on the provider's side (credits on
their dashboard, NOT x402) -- no dedicated budget built here, the operator
manages its top-up the same way as for GoPlus/Blockscout/CoinGecko.

Throughput: sourced from the REAL operator dashboard (07/23, real
screenshot) -- "Free" tier = **0.2 QPS** (never paid) or 3 QPS (legacy
client, not applicable here). Calibrated at 90% of 0.2 QPS -> minimum
interval 5.5s (CLAUDE.md doctrine "throughput calibrated to 90% of real
capacity, never guessed"). Careful -- do not confuse with the general docs
(``docs.twitterapi.io/introduction``), which advertise "up to 200 QPS per
client": that is the provider's infrastructure TECHNICAL capacity, not the
quota granted to THIS account per its tier -- the account's real dashboard
always takes authority over the general docs when calibrating THIS throttle.
No top-up cost so far: 9964 bonus credits granted on signup
(2 test calls = 36 credits consumed, i.e. 18 credits/profile, consistent
with $0.18/1000 -- $1 = 100,000 credits). Expected usage is very low anyway
(1 call per VC analysis, not a continuous stream)."""
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
# 0.2 QPS real (Free tier, operator dashboard) -> 5s/request at most;
# 90% margin (CLAUDE.md doctrine) -> 5.5s.
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
    """Full profile (followers/following/creation date) for an X handle.
    ``None`` if the key is missing, the account is not found, or on any
    failure -- never a bubbling exception, never a fabricated value."""
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
        logger.info("twitterapi_io: network failure (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s for @%s", r.status_code, handle)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- unreadable body, never a bubbling exception
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
    """Latest tweets (date + engagement) for an X handle -- used for the
    activity/regularity and engagement of the X Substance signal. ``None`` if
    the key is missing or on any failure; never a bubbling exception."""
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
        logger.info("twitterapi_io: network failure last_tweets (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s for last_tweets @%s", r.status_code, handle)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- unreadable body, never a bubbling exception
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
