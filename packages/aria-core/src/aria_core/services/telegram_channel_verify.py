"""Read-only client for a THIRD-PARTY channel/group declared by a project
(19/07, operator feedback: "is telegram possible?") -- unrelated to ARIA's own
Telegram bot (``gateway/telegram_bot.py``), no token can/should be reused
here.

`t.me/s/<channel>` (Telegram's public HTML preview page, verified live,
19/07) requires NO bot token -- Telegram exposes it for web indexing of any
PUBLIC channel. A nonexistent channel or one with no public history redirects
to `t.me/<channel>` (without the `/s/`) -- a reliable signal, empirically
verified (redirection confirmed, code 200 on the final page without the
`/s/` prefix). Fragile by nature (depends on Telegram's HTML, no contractual
stability guarantee unlike a real API) -- systematic honest degradation if the
expected format isn't found, never a fabricated figure."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_HANDLE_RE = re.compile(r"t\.me/(?:s/)?([\w\-]+)", re.IGNORECASE)
_SUBSCRIBER_RE = re.compile(
    r'counter_value">([\d.,]+[KMB]?)</span>\s*<span class="counter_type">subscribers',
)
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class TelegramChannelVerification:
    available: bool
    exists: bool | None = None  # None = never resolved, False = no public channel/history
    subscriber_count_display: str | None = None  # raw Telegram text ("11.6M") -- never reparsed into an int, ambiguous formats
    days_since_last_post: int | None = None
    error: str | None = None


def _parse_handle(url: str) -> str | None:
    m = _HANDLE_RE.search(url or "")
    if not m:
        return None
    handle = m.group(1).strip("/")
    return handle or None


async def verify_channel(url: str) -> TelegramChannelVerification:
    """Verifies a declared Telegram channel. Never an exception propagating."""
    handle = _parse_handle(url)
    if not handle:
        return TelegramChannelVerification(available=False, error="URL Telegram illisible")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            res = await client.get(f"https://t.me/s/{handle}")
    except Exception as exc:  # noqa: BLE001
        logger.info("telegram_channel_verify: request failed for %s (%s)", handle, exc)
        return TelegramChannelVerification(available=False, error=f"requête échouée ({exc})")

    if res.status_code != 200:
        return TelegramChannelVerification(available=False, error=f"HTTP {res.status_code}")

    # Redirect to the page without `/s/` -- no public channel with history
    # (doesn't exist, is private, or has never posted publicly).
    if "/s/" not in str(res.url):
        return TelegramChannelVerification(available=True, exists=False)

    text = res.text
    sub_match = _SUBSCRIBER_RE.search(text)
    subscriber_display = sub_match.group(1) if sub_match else None

    time_matches = _TIME_RE.findall(text)
    days_since_last_post = None
    if time_matches:
        try:
            last_dt = datetime.fromisoformat(time_matches[-1])
            days_since_last_post = max(0, (datetime.now(timezone.utc) - last_dt).days)
        except ValueError:
            pass

    return TelegramChannelVerification(
        available=True, exists=True,
        subscriber_count_display=subscriber_display,
        days_since_last_post=days_since_last_post,
    )


def format_channel_verification(v: TelegramChannelVerification) -> str:
    if not v.available:
        return "vérification indisponible"
    if v.exists is False:
        return "canal introuvable ou sans historique public (lien mort ou privé -- signal négatif)"
    parts = []
    if v.subscriber_count_display:
        parts.append(f"{v.subscriber_count_display} abonnés")
    if v.days_since_last_post is not None:
        parts.append(f"dernier message il y a {v.days_since_last_post}j")
    return ", ".join(parts) if parts else "canal trouvé, détails indisponibles"
