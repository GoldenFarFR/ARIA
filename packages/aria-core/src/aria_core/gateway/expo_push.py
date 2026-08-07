"""Native Expo push notifications for the operator mobile app (07/08 follow-up
to Item #201). Mirrors a subset of what already goes to Telegram -- never a
second source of truth, always a best-effort echo of an already-sent Telegram
message, so a push failure never affects the trading loop itself.

Three Android notification channels, IDs matched on the mobile side
(mobile/push.ts): the operator explicitly asked for separate channels so each
can be muted independently on the phone.
"""

from __future__ import annotations

import html
import logging
import re

import httpx

from aria_core.push_tokens import list_push_tokens

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_BATCH_SIZE = 100  # Expo's own hard cap per request

CHANNEL_TRADING = "trading"
CHANNEL_SUPPORT = "support"
CHANNEL_DISCUSSION = "discussion"

# 08/07 -- operator request: only real executions (buy/sell/partial exit) and
# the periodic 30-min open-position tracking, NEVER a pending/watching limit
# order (too noisy, not an actual trade). Detected by content -- the same
# doctrine already used by send_trading_notification for its own HTML-vs-plain
# switch (a literal substring in the formatted text), not a second parallel
# classification to keep in sync by hand at every one of the ~15 call sites.
_TRADING_PUSH_MARKERS = (
    "ACHAT FICTIF",
    "VENTE FICTIVE",
    "PRISE DE PROFIT PARTIELLE FICTIVE",
    "suivi positions ouvertes",
)

_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _derive_title_body(text: str, *, fallback_title: str) -> tuple[str, str]:
    lines = [_plain_text(line) for line in text.splitlines() if _plain_text(line)]
    if not lines:
        return fallback_title, ""
    # Line 0 is always the generic "🧪 SIMULATION..." header -- line 1 (when
    # present) is the actual event ("ACHAT FICTIF X", "VENTE FICTIVE X...").
    title = lines[1] if len(lines) > 1 else lines[0]
    body = " · ".join(lines[2:5]) if len(lines) > 2 else lines[0]
    return title[:120], body[:180]


async def send_expo_push(title: str, body: str, *, channel_id: str) -> bool:
    """Best-effort, never raises. False if unconfigured (no token registered)
    or the whole call failed -- callers must not treat this as fatal."""
    tokens = await list_push_tokens()
    if not tokens:
        return False

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "channelId": channel_id,
        }
        for token in tokens
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for i in range(0, len(messages), _BATCH_SIZE):
                batch = messages[i : i + _BATCH_SIZE]
                resp = await client.post(
                    EXPO_PUSH_URL,
                    json=batch,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    logger.warning("Expo push send failed: HTTP %s %s", resp.status_code, resp.text[:200])
        return True
    except Exception as exc:  # noqa: BLE001 -- a push failure must never break the trading cycle
        logger.warning("Expo push send failed: %s", exc)
        return False


async def notify_trading(text: str) -> bool:
    """Echoes the trading Telegram topic (send_trading_notification) to the
    ``trading`` push channel -- ONLY for buy/sell/partial-exit/periodic
    tracking, never a pending/watching limit-order alert (see
    _TRADING_PUSH_MARKERS)."""
    if not any(marker in text for marker in _TRADING_PUSH_MARKERS):
        return False
    title, body = _derive_title_body(text, fallback_title="ARIA — Trading")
    return await send_expo_push(title, body, channel_id=CHANNEL_TRADING)


async def notify_support(text: str) -> bool:
    """Echoes the generic admin Telegram DM (_notify_telegram, ~20 heartbeat
    cycles: conviction alerts, wallet monitor, reports, watchdogs...) to the
    ``support`` push channel. No content filter -- this channel IS the
    catch-all, muting it on the phone is the operator's own lever."""
    title, body = _derive_title_body(text, fallback_title="ARIA — Suivi")
    return await send_expo_push(title, body, channel_id=CHANNEL_SUPPORT)
