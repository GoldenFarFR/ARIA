"""Autonomous visual identity — operator anchor → Grok Imagine → avatar + X banner."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def visual_autonomy_enabled() -> bool:
    """Unified avatar+banner cycle (replaces separate banner curiosity + style)."""
    env = os.environ.get("ARIA_VISUAL_AUTONOMY", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    from aria_core.runtime import settings

    if getattr(settings, "aria_autonomous", False):
        return True
    return bool(getattr(settings, "aria_visual_autonomy", False))


def visual_auto_apply_enabled() -> bool:
    """Apply without /avatar style apply (autonomous ZHC mode)."""
    env = os.environ.get("ARIA_VISUAL_AUTO_APPLY", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    from aria_core.runtime import settings

    if not getattr(settings, "aria_autonomous", False):
        return False
    return bool(getattr(settings, "aria_visual_auto_apply", True))


def _banner_stale_vs_avatar() -> bool:
    from aria_core.avatar import current_avatar_path

    banner = __import__("aria_core.x_banner", fromlist=["x_banner_path"]).x_banner_path()
    av = current_avatar_path()
    if not banner.is_file() or not av.is_file():
        return True
    return av.stat().st_mtime > banner.stat().st_mtime


async def refresh_x_banner_autonomous(*, force: bool = False) -> dict[str, Any]:
    from aria_core.x_banner import ensure_x_banner_file, x_banner_path
    from aria_core.gateway.x_twitter import apply_profile_banner

    path = x_banner_path()
    if force and path.is_file():
        path.unlink(missing_ok=True)
    generated = await ensure_x_banner_file(force=force)
    if not generated:
        return {"ok": False, "reason": "generate_failed"}
    uploaded = await apply_profile_banner(generated)
    return {"ok": uploaded, "path": str(generated), "uploaded": uploaded}


async def run_visual_autonomy_cycle(
    *, lang: str = "fr", notify: bool = True, force: bool = False,
) -> dict[str, Any]:
    """
    1. Anchors identity from current.jpg (operator's base photo)
    2. New Imagine style if due → auto-apply + Telegram/X sync
    3. 3:1 banner regenerated if missing or avatar is more recent
    """
    from aria_core.avatar_identity import ensure_identity_anchor_from_current, has_identity_anchor
    from aria_core.avatar_style_refresh import is_image_generation_available, run_refresh_cycle
    from aria_core.x_banner import x_banner_path

    if not visual_autonomy_enabled():
        return {"skipped": True, "reason": "disabled"}

    ensure_identity_anchor_from_current()
    if not has_identity_anchor():
        return {"skipped": True, "reason": "no_identity_anchor"}

    out: dict[str, Any] = {"ok": True}
    auto = visual_auto_apply_enabled()

    avatar = await run_refresh_cycle(
        notify=notify and not auto, auto_apply=auto, force=force,
    )
    out["avatar"] = avatar

    # Banner = separate brand asset — no auto-regen when the avatar changes (avoids a face on the header)
    banner_refresh = (
        bool(getattr(__import__("aria_core.runtime", fromlist=["settings"]).settings, "aria_banner_auto_refresh", True))
        and is_image_generation_available()
        and (force or not x_banner_path().is_file())
    )
    if banner_refresh:
        out["banner"] = await refresh_x_banner_autonomous(force=force)
        if notify and out["banner"].get("uploaded"):
            await _notify_visual_update(out, lang=lang)

    try:
        from aria_core.x_profile import sync_x_profile

        out["profile"] = await sync_x_profile()
    except ModuleNotFoundError:
        out["profile"] = {"skipped": True, "reason": "x_profile_unavailable"}

    return out


async def _notify_visual_update(result: dict[str, Any], *, lang: str = "fr") -> None:
    try:
        from aria_core.gateway.telegram_bot import send_message

        av = result.get("avatar") or {}
        bn = result.get("banner") or {}
        if lang == "fr":
            lines = ["🎨 Identité visuelle ARIA — mise à jour auto (Grok Imagine)"]
            if av.get("applied"):
                cur = (av.get("current") or {}).get("note", "")
                lines.append(f"Avatar : appliqué — {cur[:120]}")
            if bn.get("uploaded"):
                lines.append("Bannière X : régénérée et publiée (3:1)")
            lines.append("Source : ancre identité + xAI Imagine")
        else:
            lines = ["🎨 ARIA visual identity — auto update (Grok Imagine)"]
            if av.get("applied"):
                lines.append("Avatar: applied")
            if bn.get("uploaded"):
                lines.append("X banner: regenerated")
        await send_message("\n".join(lines))
    except Exception as exc:
        logger.warning("Visual autonomy notify failed: %s", exc)