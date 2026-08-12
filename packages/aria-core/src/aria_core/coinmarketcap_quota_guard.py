"""Proactive CoinMarketCap MONTHLY credit-quota guard (12/08) -- found live
the same day: the real VPS key was confirmed at 14569/15000 monthly credits
used (97%) via a real ``/v1/key/info`` call, while ``services/coinmarketcap.py``
only ever throttled the PER-MINUTE rate (``_MIN_INTERVAL``, calibrated to the
90%-throughput doctrine) -- zero protection against the monthly quota itself
running out mid-cycle. Different shape from ``goplus_quota_suspension.py``
(reactive, arms only after real consecutive 429/4029 failures) because CMC
exposes the REAL remaining quota directly and for free (``/v1/key/info``
itself never consumes a credit, confirmed live: ``status.credit_count == 0``
on the same response) -- proactive polling of the true number beats waiting
to fail first and wasting credits discovering it.

SQL plumbing shared with the other quota/outage guards via
``single_row_state.SingleRowStore`` (same factored-out pattern, 10/08)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Refresh the real quota at most every 10 minutes -- /v1/key/info costs zero
# credits, but this client is called many times per momentum cycle and there
# is no reason to hit the network on every single one just to re-read a
# number that only changes as fast as real CMC calls are actually made.
_CACHE_TTL_SECONDS = 600

# Suspend once remaining monthly credits drop under 5% of the plan limit --
# leaves enough headroom that a suspension always arms BEFORE the account
# hits a hard 0, never a guessed "close enough" cutoff (the real ratio is
# read from the API on every refresh, never estimated locally).
_SUSPEND_THRESHOLD_RATIO = 0.05

_TABLE = "coinmarketcap_quota_guard_state"
_COLUMNS = [
    ("credits_left", "INTEGER", None),
    ("credit_limit_monthly", "INTEGER", None),
    ("checked_at", "TEXT", None),
    ("suspended", "INTEGER NOT NULL DEFAULT 0", 0),
]


def _store() -> SingleRowStore:
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def _fetch_real_quota() -> tuple[int, int] | None:
    """Real, uncached call to /v1/key/info -- never consumes a credit
    (confirmed live). Returns (credits_left, credit_limit_monthly), or None
    on any failure (network/auth/shape) -- never fabricated."""
    import os

    api_key = os.environ.get("COINMARKETCAP_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://pro-api.coinmarketcap.com/v1/key/info",
                headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
            )
        response.raise_for_status()
        payload = response.json()
        usage = payload["data"]["usage"]["current_month"]
        plan = payload["data"]["plan"]
        return int(usage["credits_left"]), int(plan["credit_limit_monthly"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning("coinmarketcap_quota_guard: could not read /v1/key/info -> %s", exc)
        return None


async def is_suspended() -> bool:
    """Checked FIRST in ``coinmarketcap._get_json``, before any real pricing
    call. Fail-open on an unreadable quota (never blocks the pipeline on a
    guard that itself can't get a real number -- the underlying call's own
    429/5xx handling stays the backstop either way)."""
    row = await _store().read("credits_left", "credit_limit_monthly", "checked_at", "suspended")
    credits_left, limit, checked_at_raw, suspended = row or (None, None, None, 0)
    checked_at = parse_iso(checked_at_raw)
    now = datetime.now(timezone.utc)

    stale = checked_at is None or (now - checked_at) > timedelta(seconds=_CACHE_TTL_SECONDS)
    if not stale:
        return bool(suspended)

    fetched = await _fetch_real_quota()
    if fetched is None:
        # Can't confirm the real number right now -- keep whatever the last
        # known state was rather than guessing either way.
        return bool(suspended)

    credits_left, limit = fetched
    ratio_left = (credits_left / limit) if limit else 1.0
    now_suspended = ratio_left < _SUSPEND_THRESHOLD_RATIO
    was_suspended = bool(suspended)

    await _store().write(
        {
            "credits_left": credits_left,
            "credit_limit_monthly": limit,
            "checked_at": now.isoformat(),
            "suspended": int(now_suspended),
        }
    )

    if now_suspended and not was_suspended:
        await _notify_armed(credits_left, limit)
    elif was_suspended and not now_suspended:
        logger.info("coinmarketcap_quota_guard: disarmed, credits_left=%s/%s", credits_left, limit)

    return now_suspended


async def _notify_armed(credits_left: int, limit: int) -> None:
    from aria_core.gateway.telegram_bot import send_message

    await send_message(
        "🛡️ Suspension automatique CoinMarketCap activée -- quota mensuel de crédits "
        f"presque épuisé ({credits_left}/{limit} restants). Reprise automatique dès que "
        "le quota redevient suffisant (reset mensuel, ou refresh manuel du plan). "
        "Aucune action requise -- CMC n'est qu'une 3e couche de repli, GeckoTerminal/"
        "DexScreener restent actifs."
    )
