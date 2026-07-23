"""twit.sh client (x402) — X search/timeline, COMPLEMENT to
``aria_core.gateway.x_twitter`` (07/19, #111/#112, operator decision settled
via AskUserQuestion: never a replacement, never the primary source). Found
via x402scan.com (operator screenshots, $667 volume/24h — the most-used X
service in the whole Bazaar ecosystem, 91,520 calls/30d), validated under
real conditions 4 times (2 quality payments + 2 schema-verification ones,
same segment): ``x402.twit.sh/tweets/search`` ($0.006/call, ``words``
param) and ``x402.twit.sh/tweets/user`` ($0.01/call, ``username`` param --
confirmed after a first attempt with ``from`` returned 400 "Missing required
query parameter: username").

X API v2-compatible schema (``id``/``text``/``created_at``/``author_id``/
``public_metrics``) BUT ``created_at`` in legacy Twitter v1.1 format ("Sun Jul
19 15:48:00 +0000 2026", not ISO 8601) -- normalized here to ISO so the
result is a DROP-IN of the same shape as
``x_twitter.search_recent_tweets``/``fetch_user_recent_tweets`` ({"text",
"created_at", ...}): ``conviction_research.py`` needs no separate parsing
branch for the fallback.

Every call goes through ``x402_executor.fetch_paid_resource`` (SHARED weekly
cap ``x402_budget.py``, $5/week, ``/stop`` circuit breaker, dedicated CDP
wallet) -- same guardrail as ``services/ottoai.py``/``services/cybercentry.py``.
No additional dedicated cap built here: the shared cap, already fail-closed,
bounds the worst case if this fallback is used often."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://x402.twit.sh/tweets/search"
_USER_URL = "https://x402.twit.sh/tweets/user"

# Legacy Twitter v1.1 format observed under real conditions (07/19) -- distinct
# from the ISO 8601 returned by the official X API (x_twitter.py).
_LEGACY_DATE_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _normalize_created_at(raw: object) -> str | None:
    """Converts the legacy Twitter v1.1 format to ISO 8601 -- never an
    exception. Without this normalization, ``_posting_cadence_from_tweets``
    (conviction_research.py) silently fails to parse EVERY twit.sh tweet
    (ValueError caught, ignored), making posting cadence systematically
    "unknown" despite real data -- real bug avoided by checking the format
    against a real call before coding (07/14 standard)."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw, _LEGACY_DATE_FORMAT)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return raw


def _parse_tweets(body: bytes) -> list[dict]:
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 -- unreadable body, never an exception bubbling up
        return []
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        return []
    tweets: list[dict] = []
    for t in data:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        tweets.append(
            {
                "text": text,
                "created_at": _normalize_created_at(t.get("created_at")),
                "tweet_id": t.get("id"),
                "author_id": t.get("author_id"),
                "public_metrics": t.get("public_metrics") or {},
            }
        )
    return tweets


async def search_tweets(
    query: str, *, max_results: int = 10, contract: str = "", token_symbol: str = "",
) -> list[dict]:
    """Free-form X search (ticker/address/keyword), x402 ($0.006/call).
    Standard guardrail: never an exception bubbling up, empty list on any
    failure. ``contract``/``token_symbol`` (07/19, #143): passed through as-is
    to the x402_budget log, so the payment stays traceable to the token concerned."""
    q = (query or "").strip()
    if not q:
        return []
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    params = urlencode({"words": q[:500], "maxResults": max(10, min(int(max_results), 100))})
    try:
        result = await x402_executor.fetch_paid_resource(
            f"{_SEARCH_URL}?{params}",
            resource="tweets-search",
            provider="twitsh",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
            contract=contract,
            token_symbol=token_symbol,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("twitsh: search_tweets failed (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_tweets(result.body)


async def fetch_user_tweets(
    username: str, *, max_results: int = 10, contract: str = "", token_symbol: str = "",
) -> list[dict]:
    """Recent timeline of an X account by its handle, x402 ($0.01/call). Same
    guardrail as ``search_tweets``, same ``contract``/``token_symbol`` parameters."""
    handle = (username or "").lstrip("@").strip()
    if not handle:
        return []
    from aria_core import x402_executor
    from aria_core.agent_wallet_cdp_adapter import usdc_balance_usd
    from aria_core.x402_cdp_signer import build_x402_payment_header

    params = urlencode({"username": handle, "maxResults": max(5, min(int(max_results), 100))})
    try:
        result = await x402_executor.fetch_paid_resource(
            f"{_USER_URL}?{params}",
            resource="tweets-user",
            provider="twitsh",
            balance_fn=usdc_balance_usd,
            pay_fn=build_x402_payment_header,
            contract=contract,
            token_symbol=token_symbol,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("twitsh: fetch_user_tweets failed (%s)", exc)
        return []
    if result.status != "ok" or not result.body:
        return []
    return _parse_tweets(result.body)
