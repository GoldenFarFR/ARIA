"""Capability gap -> operator notification (never an external action).

History (10/07): this module used to open GitHub issues/PRs and delegate to
an external "Cursor worker" (aria_worker_queue.py) whenever ARIA judged
herself blocked -- reachable without any operator validation (heartbeat,
everyday Telegram messages, the site's public form). This mechanism
contradicted the project's doctrine ("Cursor/Grok abandoned, Claude Code
handles 100% of the building") and had already written for real to this repo
(issue #1 + PR #2, 03/07). Removed: ARIA now notifies the operator, she never
opens a ticket or delegates code to a third party.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory

logger = logging.getLogger(__name__)

DEDUP_DAYS = 7

CAPABILITY_TITLES: dict[str, str] = {
    "x_profile_banner": "Capacite: banniere profil X (update_profile_banner)",
    "x_oauth_write": "Capacite: cles X OAuth Read+Write configurees",
    "image_api_key": "Capacite: generation banniere X 3:1 (IMAGE_API_KEY)",
    "identity_anchor": "Capacite: ancre identite visage",
    "x_banner_generate": "Capacite: asset banniere X local (x_banner.jpg 3:1)",
    "health_render_regression": "Incident: regression health Render (3 echecs)",
}


def _gaps_dir() -> Path:
    from aria_core.paths import data_dir

    path = data_dir() / "capability-gaps"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_record(capability_id: str) -> dict[str, Any] | None:
    path = _gaps_dir() / f"{capability_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_record(capability_id: str, record: dict[str, Any]) -> None:
    path = _gaps_dir() / f"{capability_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _recently_filed(capability_id: str) -> dict[str, Any] | None:
    rec = _load_record(capability_id)
    if not rec or not rec.get("filed_at"):
        return None
    try:
        filed = datetime.fromisoformat(rec["filed_at"])
        if filed.tzinfo is None:
            filed = filed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) - filed < timedelta(days=DEDUP_DAYS):
        return rec
    return None


async def gap_runtime_resolved(capability_id: str) -> bool:
    """True if the gap should no longer be reported (capability now available)."""
    if capability_available(capability_id):
        return True
    if capability_id == "health_render_regression":
        try:
            from aria_core.health_watch import probe_health_ok

            return await probe_health_ok()
        except Exception:
            return False
    return False


def capability_available(capability_id: str) -> bool:
    """Lightweight introspection -- True if the code appears present."""
    if capability_id == "x_profile_banner":
        try:
            from aria_core.gateway.x_twitter import apply_profile_banner  # noqa: F401
            return True
        except ImportError:
            return False
    if capability_id == "x_oauth_write":
        from aria_core.gateway.x_twitter import is_x_post_configured
        return is_x_post_configured()
    if capability_id == "image_api_key":
        from aria_core.portrait_scene import _image_api_key
        return bool(_image_api_key())
    if capability_id == "identity_anchor":
        from aria_core.avatar_identity import has_identity_anchor
        return has_identity_anchor()
    return False


async def file_capability_gap(
    capability_id: str,
    *,
    context: str = "",
    lang: str = "fr",
) -> dict[str, Any]:
    """Reports a capability gap -- Telegram notification only.

    7-day dedup per capability_id (local, ``DATA_DIR/capability-gaps``).
    No longer opens an issue, no longer creates a PR/branch, no longer
    delegates to an external tool -- see the module docstring.
    """
    if await gap_runtime_resolved(capability_id):
        return {
            "status": "skipped_resolved",
            "capability_id": capability_id,
            "reason": "runtime_ok",
        }

    existing = _recently_filed(capability_id)
    if existing:
        return {
            "status": "dedup",
            "capability_id": capability_id,
            "filed_at": existing.get("filed_at"),
        }

    title = CAPABILITY_TITLES.get(capability_id, f"Capacite manquante: {capability_id}")
    record: dict[str, Any] = {
        "capability_id": capability_id,
        "title": title,
        "context": context[:2000],
        "filed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_record(capability_id, record)
    append_memory("self-improve", f"[cap-gap] {capability_id} — {title}")
    await _notify_gap(capability_id, record, lang=lang)
    return {**record, "status": "logged"}


def count_resolved_gaps(*, days: int = 7) -> int:
    """Recently reported gaps whose capability is now available."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    resolved = 0
    gaps_path = _gaps_dir()
    if not gaps_path.is_dir():
        return 0
    for path in gaps_path.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        filed_at = rec.get("filed_at")
        cap_id = rec.get("capability_id") or path.stem
        if not filed_at:
            continue
        try:
            filed = datetime.fromisoformat(filed_at)
            if filed.tzinfo is None:
                filed = filed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if filed < cutoff:
            continue
        if capability_available(cap_id):
            resolved += 1
    return resolved


async def _notify_gap(capability_id: str, record: dict[str, Any], *, lang: str) -> None:
    try:
        from aria_core.gateway.telegram_bot import notify_admin
    except ImportError:
        return

    if lang == "fr":
        msg = f"ARIA — lacune capacite detectee\n\nID : {capability_id}\n{record.get('title', '')}\n"
    else:
        msg = f"ARIA — capability gap: {capability_id}\n{record.get('title', '')}\n"
    if record.get("context"):
        msg += f"\n{record['context'][:300]}"
    await notify_admin(msg.strip())


def format_gap_reply(result: dict[str, Any], *, lang: str = "fr") -> str:
    if result.get("status") == "dedup":
        if lang == "fr":
            return "Deja signale cette semaine pour cette capacite."
        return "Already reported this week for this capability."

    if lang == "fr":
        return f"Lacune notee et signalee (Telegram) : {result.get('capability_id', '')}."
    return f"Gap logged and reported (Telegram): {result.get('capability_id', '')}."
