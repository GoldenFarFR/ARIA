"""Cooldown curriculum — persiste sur disque (survit aux redeploy Render)."""
from __future__ import annotations

from datetime import datetime, timezone


def minutes_since_iso(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def cooldown_minutes_remaining(last_run: str | None, *, interval_minutes: int) -> int:
    """Minutes restantes avant le prochain envoi (0 = OK)."""
    since = minutes_since_iso(last_run)
    if since is None:
        return 0
    remaining = int(interval_minutes - since)
    return max(0, remaining)