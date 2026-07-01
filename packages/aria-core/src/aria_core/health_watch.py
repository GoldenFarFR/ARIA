"""Surveillance health Render — regression -> issue auto (Phase 3b)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from aria_core.memory import append_memory
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

_FAIL_STREAK = 0
_LAST_OK: datetime | None = None
REGRESSION_THRESHOLD = 3


async def _probe_health() -> tuple[bool, str]:
    bases: list[str] = []
    site = (getattr(settings, "site_base_url", None) or "").strip().rstrip("/")
    if site:
        bases.append(site)
    port = os.getenv("PORT", "10000")
    bases.append(f"http://127.0.0.1:{port}")

    last_err = "no endpoint"
    seen: set[str] = set()
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        url = f"{base}/api/health"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    body = r.json()
                    if body.get("status") == "ok":
                        return True, f"health ok @ {base} commit={body.get('commit', '?')}"
                last_err = f"{url} -> HTTP {r.status_code}"
        except Exception as exc:
            last_err = f"{url} -> {exc}"
    return False, last_err


async def check_health_regression() -> dict[str, Any]:
    """Ping health; apres 3 echecs consecutifs, ouvre issue health_render_regression."""
    global _FAIL_STREAK, _LAST_OK

    ok, detail = await _probe_health()
    if ok:
        _FAIL_STREAK = 0
        _LAST_OK = datetime.now(timezone.utc)
        return {"ok": True, "streak": 0, "detail": detail, "last_ok": _LAST_OK.isoformat()}

    _FAIL_STREAK += 1
    append_memory("heartbeat", f"[health_watch] fail {_FAIL_STREAK}/{REGRESSION_THRESHOLD}: {detail[:200]}")
    result: dict[str, Any] = {
        "ok": False,
        "streak": _FAIL_STREAK,
        "detail": detail,
        "last_ok": _LAST_OK.isoformat() if _LAST_OK else None,
    }

    if _FAIL_STREAK >= REGRESSION_THRESHOLD:
        from aria_core.capability_gap import file_capability_gap

        ctx = (
            f"{REGRESSION_THRESHOLD} echecs consecutifs health\n"
            f"Dernier: {detail}\n"
            f"Dernier OK: {result['last_ok'] or 'jamais'}"
        )
        gap = await file_capability_gap(
            "health_render_regression",
            context=ctx,
            lang="fr",
            open_pr=False,
        )
        result["gap"] = gap
        _FAIL_STREAK = 0

    return result