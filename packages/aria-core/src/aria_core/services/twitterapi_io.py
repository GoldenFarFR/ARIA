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

``search_tweets`` (12/08) -- free-form keyword search (``GET
/twitter/tweet/advanced_search``, verified live against docs.twitterapi.io:
supports ``from:``/``since_time:``/``until_time:``/exact-phrase/OR/AND
operators, 0.00015$/tweet returned, i.e. 15 credits/tweet at the account's
own 1$=100,000-credits rate). Two real uses, both explicit operator
decisions (12/08): (1) replaces the paid official-X bearer path
(``gateway.x_twitter.search_recent_tweets``, ~33x more expensive per read
and requires an active X API subscription the operator just cancelled) as
``conviction_research.py``'s primary buzz-search tier; (2) the FIRST tier of
the bonding "detective mode" (``project_name``-based search, tried when
Virtuals declares no official link at all) -- both keep Tavily as the
fallback when this is unconfigured/fails. Response shape (``createdAt`` in
Twitter's own RFC-2822-like format, e.g. "Tue Dec 10 07:00:30 +0000 2024",
parsed with its own helper) verified live via WebFetch on
docs.twitterapi.io/api-reference/endpoint/tweet_advanced_search.

**27/08, real incident**: this docstring used to claim ``fetch_last_tweets``
returned ISO8601 "verified live" (07/23) -- re-verified live and found
FALSE: it returns this SAME legacy RFC-2822-like format, not ISO8601. Only
``fetch_user_profile`` (``/user/info``) genuinely returns ISO8601. See
``_parse_created_at``'s own docstring for the real, currently-live incident
this caused (``x_substance.py``'s regularity criterion silently scored
0/100 for every account since this shipped).

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
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.twitterapi.io/twitter/user/info"
_LAST_TWEETS_URL = "https://api.twitterapi.io/twitter/user/last_tweets"
_SEARCH_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
_BALANCE_URL = "https://api.twitterapi.io/oapi/my/info"
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
    # Item #171, 28/07 -- real gap found: a project's own declared launchpad
    # link (e.g. "app.virtuals.io/virtuals/<id>") often lives ONLY in the X
    # bio, never in a tweet or a crawlable website -- verified live on a real
    # case (HOLO) where the bio's `entities.description.urls` already
    # contains the link fully EXPANDED (no t.co unshortening needed).
    # Empty list when the field is absent/unparsable -- never None, so a
    # caller can always safely iterate.
    bio: str = ""
    bio_urls: list[str] = field(default_factory=list)


@dataclass
class TwitterApiIoBalance:
    # 13/08 -- fields verified live against the real prod key
    # (``GET /oapi/my/info``, header ``X-API-Key``):
    # {"recharge_credits": 999677, "total_bonus_credits": 0}. Built after a
    # real ~24h prepaid-credit exhaustion silently starved x_substance.py/
    # conviction_research.py and pushed their fallback traffic onto Tavily
    # (see ``twitterapi_io_budget.py``) -- this balance check is the
    # PROACTIVE signal that incident lacked (the client itself only ever
    # degrades silently to ``None`` on any HTTP failure, never distinguishing
    # "no credits" from a generic outage).
    recharge_credits: int
    bonus_credits: int


@dataclass
class TwitterApiIoTweet:
    created_at: datetime
    like_count: int
    reply_count: int
    retweet_count: int
    quote_count: int
    # 03/08 -- added for token_cashtag_engagement.py (needs to search tweet
    # bodies for a token's own symbol). Additive field, default "" so every
    # existing caller (x_substance.py) is unaffected.
    text: str = ""


def is_twitterapi_io_configured() -> bool:
    return bool(os.environ.get("TWITTERAPI_IO_KEY", "").strip())


_LEGACY_TWITTER_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _parse_twitter_format_created_at(raw: object) -> str | None:
    """``advanced_search``'s own ``createdAt`` shape (e.g. "Tue Dec 10
    07:00:30 +0000 2024") -- 27/08: ``fetch_last_tweets`` turned out to use
    this SAME legacy shape too (see ``_parse_created_at``'s own docstring),
    not the ISO8601 this module's docstring used to claim. Returns the
    ISO8601 string form (never the raw datetime object) so callers get the
    exact same shape as ``gateway.x_twitter``'s own ``created_at`` field --
    ``None`` on anything unparsable, never a fabricated timestamp."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, _LEGACY_TWITTER_DATE_FORMAT).isoformat()
    except ValueError:
        return None


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


def _parse_created_at(raw: object) -> datetime | None:
    """``/user/info`` genuinely returns ISO8601 (verified live 27/08). But
    ``/user/last_tweets`` was ALSO assumed to (this module's own docstring
    claimed it was "verified live" on 07/23) -- re-verified live 27/08 and
    found FALSE: it returns the same legacy Twitter v1.1 format as
    ``search_tweets`` ("Tue Aug 25 18:44:11 +0000 2026"), which
    ``fromisoformat`` cannot parse. Every call silently returned ``None``,
    making ``fetch_last_tweets`` return an always-empty list regardless of
    the account -- ``x_substance.py``'s regularity criterion (25% weight)
    scored 0/100 for every account evaluated since this shipped, a silent
    bias never caught because the test suite's own mocks used fabricated ISO
    dates rather than the real shape. ISO tried first (still the real shape
    for ``/user/info``), legacy format as fallback -- never the reverse, to
    keep the fast/common path first."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, _LEGACY_TWITTER_DATE_FORMAT).astimezone(timezone.utc)
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

    # Item #171, 28/07: `entities.description.urls[].expanded_url` is the
    # bio's t.co-shortened links ALREADY expanded by the API itself -- never
    # needs a second unshortening round-trip. Best-effort: any malformed
    # shape here degrades to an empty list, never a crash on an otherwise
    # valid profile.
    bio = data.get("description") if isinstance(data.get("description"), str) else ""
    bio_urls: list[str] = []
    try:
        for entry in data.get("entities", {}).get("description", {}).get("urls", []):
            expanded = entry.get("expanded_url")
            if isinstance(expanded, str) and expanded:
                bio_urls.append(expanded)
    except (AttributeError, TypeError):
        bio_urls = []

    return TwitterApiIoProfile(
        followers=followers, following=following, created_at=created_at, bio=bio, bio_urls=bio_urls,
    )


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
    data = payload.get("data")
    raw_tweets = data.get("tweets") if isinstance(data, dict) else None
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
                text=str(item.get("text") or ""),
            )
        )
    return tweets or None


async def search_tweets(query: str, *, max_results: int = 10) -> list[dict] | None:
    """Free-form keyword search (``GET /twitter/tweet/advanced_search``) --
    same role as ``gateway.x_twitter.search_recent_tweets`` (buzz search on a
    ticker/contract/project name) but ~33x cheaper per read and doesn't
    require an active official X API subscription. ``None`` if the key is
    missing or on any failure -- never a bubbling exception, never a
    fabricated tweet.

    Output shape matches ``search_recent_tweets``'s own dicts (``text``/
    ``created_at``/``tweet_id``/``author_id``) so callers (conviction_research.py)
    don't need a provider-specific branch -- ``author_id`` here is the
    author's @handle (twitterapi.io exposes no numeric id), never confused
    with a real X API author_id."""
    q = (query or "").strip()
    if not q:
        return None

    api_key = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(
                _SEARCH_URL,
                params={"query": q[:500], "queryType": "Latest"},
                headers={"X-API-Key": api_key},
            )
    except httpx.TransportError as exc:
        logger.info("twitterapi_io: network failure search_tweets (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s for search_tweets %r", r.status_code, q)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- unreadable body, never a bubbling exception
        return None

    if not isinstance(payload, dict):
        return None
    raw_tweets = payload.get("tweets")
    if not isinstance(raw_tweets, list):
        return None

    tweets: list[dict] = []
    for item in raw_tweets[: max(1, min(int(max_results), 100))]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        tweets.append({
            "text": text,
            "created_at": _parse_twitter_format_created_at(item.get("createdAt")),
            "tweet_id": item.get("id"),
            "author_id": author.get("userName"),
        })
    return tweets or None


async def fetch_credit_balance() -> TwitterApiIoBalance | None:
    """Real prepaid-credit balance on the account (``GET /oapi/my/info``).
    ``None`` if the key is missing or on any failure -- never a bubbling
    exception, never a fabricated balance. Consumed by
    ``twitterapi_io_budget.py``, never by ``x_substance.py``/
    ``conviction_research.py`` (unrelated concerns)."""
    api_key = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(_BALANCE_URL, headers={"X-API-Key": api_key})
    except httpx.TransportError as exc:
        logger.info("twitterapi_io: network failure fetch_credit_balance (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("twitterapi_io: HTTP %s for fetch_credit_balance", r.status_code)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- unreadable body, never a bubbling exception
        return None

    if not isinstance(payload, dict):
        return None
    recharge = payload.get("recharge_credits")
    bonus = payload.get("total_bonus_credits")
    if not isinstance(recharge, int) or not isinstance(bonus, int):
        return None

    return TwitterApiIoBalance(recharge_credits=recharge, bonus_credits=bonus)
