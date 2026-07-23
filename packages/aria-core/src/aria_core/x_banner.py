"""ARIA X banner — 3:1 profile header (different from the square avatar).

Visual pipeline (3 distinct assets):
- **Avatar**: `current.jpg` — square profile photo (Telegram /avatar, X sync).
- **Identity anchor**: `identity_anchor.jpg` — face reference (same character).
- **Banner**: `x_banner.jpg` — 3:1 creative Imagine header (text-to-image brand, not a profile photo).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aria_core.paths import aria_avatar_dir

logger = logging.getLogger(__name__)

BANNER_FILENAME = "x_banner.jpg"
# Recommended X header 1500×500 (3:1) — API max 3 MB
BANNER_WIDTH = 1500
BANNER_HEIGHT = 500
BANNER_MAX_BYTES = 3 * 1024 * 1024


def x_banner_path() -> Path:
    return aria_avatar_dir() / BANNER_FILENAME


def normalize_banner_jpeg(
    data: bytes,
    *,
    width: int = BANNER_WIDTH,
    height: int = BANNER_HEIGHT,
    max_bytes: int = BANNER_MAX_BYTES,
) -> bytes:
    """Center-crops to 3:1 then resizes — never the 640×640 square avatar."""
    from io import BytesIO

    from PIL import Image

    target_ratio = width / height
    img = Image.open(BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    current_ratio = w / h if h else target_ratio

    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif current_ratio < target_ratio:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((width, height), Image.Resampling.LANCZOS)

    for quality in (90, 85, 80, 75, 70, 65):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=65, optimize=True)
    return buf.getvalue()


def default_banner_scene() -> str:
    return (
        "Zero-Human Company holding skyline at dusk, autonomous AI operations motif, "
        "golden data streams over dark vault geometry, Vanguard ZHC energy, "
        "cinematic depth of field, no text overlay"
    )


def _banner_brand_brief() -> str:
    """Abstract brand only — never the identity brief (avoids a face on the banner)."""
    from aria_core.runtime import settings

    holding = (getattr(settings, "aria_holding_name", "") or "").strip()
    if holding:
        return f"{holding} — Zero-Human Company, autonomous AI holding, GoldenFar Vanguard"
    return "GoldenFar Vanguard ZHC — autonomous crypto holding, dark gold fintech brand"


async def generate_x_banner_jpeg() -> bytes | None:
    from aria_core.portrait_scene import generate_banner_creative

    return await generate_banner_creative(
        brand_brief=_banner_brand_brief(),
        scene=default_banner_scene(),
    )


async def ensure_x_banner_file(*, force: bool = False) -> Path | None:
    path = x_banner_path()
    if force and path.is_file():
        path.unlink(missing_ok=True)
    if not force and path.is_file() and path.stat().st_size > 10_000:
        return path
    data = await generate_x_banner_jpeg()
    if not data:
        return None
    normalized = normalize_banner_jpeg(data)
    aria_avatar_dir().mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalized)
    return path


async def get_visual_assets_status() -> dict[str, Any]:
    """Avatar, identity anchor, and banner — separate states (don't confuse them)."""
    from aria_core.avatar import current_avatar_path
    from aria_core.avatar_identity import has_identity_anchor

    banner_local = x_banner_path().is_file()
    x_status = await get_x_banner_status()
    return {
        "avatar_profile": current_avatar_path().is_file(),
        "identity_anchor": has_identity_anchor(),
        "banner_local": banner_local,
        "banner_remote": bool(x_status.get("has_banner")),
        "x_configured": bool(x_status.get("x_configured")),
        "banner_url": x_status.get("banner_url"),
    }


def format_visual_assets_lines(*, lang: str = "fr") -> list[str]:
    """HUD lines — avatar != anchor != banner."""
    from aria_core.avatar import current_avatar_path
    from aria_core.avatar_identity import has_identity_anchor

    av = current_avatar_path().is_file()
    anc = has_identity_anchor()
    loc = x_banner_path().is_file()
    if lang == "fr":
        return [
            f"   Avatar profil (carré) : {'oui' if av else 'non'} — current.jpg",
            f"   Ancre identité (réf. visage) : {'oui' if anc else 'non'} — identity_anchor.jpg",
            f"   Bannière X (3:1, header) : fichier local {'oui' if loc else 'non'} — x_banner.jpg",
        ]
    return [
        f"   Profile avatar (square): {'yes' if av else 'no'}",
        f"   Identity anchor: {'yes' if anc else 'no'}",
        f"   X banner file (3:1): {'yes' if loc else 'no'}",
    ]


async def get_x_banner_status() -> dict[str, Any]:
    from aria_core.gateway.x_twitter import get_profile_banner_status, is_x_post_configured

    local = x_banner_path().is_file()
    remote = await get_profile_banner_status()
    return {
        "local_banner": local,
        "local_path": str(x_banner_path()) if local else None,
        "x_configured": is_x_post_configured(),
        **remote,
    }