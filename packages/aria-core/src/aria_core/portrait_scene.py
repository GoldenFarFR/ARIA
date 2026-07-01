"""Génération de portraits ARIA — même personnage, nouveau décor (API image optionnelle)."""

from __future__ import annotations

import base64
import logging
import os
from io import BytesIO
from pathlib import Path

import httpx

from aria_core.runtime import settings

logger = logging.getLogger(__name__)

XAI_IMAGE_EDIT_URL = "https://api.x.ai/v1/images/edits"
# grok-imagine-image = 0.02$/image — quality = 0.05$/image (voir docs.x.ai)
DEFAULT_IMAGE_MODEL = "grok-imagine-image"
ANCHOR_MAX_PX = 640


def _image_api_key() -> str:
    explicit = getattr(settings, "image_api_key", "") or ""
    if explicit.strip():
        return explicit.strip()
    provider = settings.llm_provider.lower()
    if provider in ("xai", "grok"):
        return (settings.llm_api_key or "").strip()
    return ""


def _image_model() -> str:
    env = os.environ.get("IMAGE_API_MODEL", "").strip()
    if env:
        return env
    return (getattr(settings, "image_api_model", None) or DEFAULT_IMAGE_MODEL).strip()


def _prepare_anchor_jpeg(anchor_jpeg: bytes) -> bytes:
    """Réduit l'ancre avant upload — même tarif Imagine, moins de latence."""
    from PIL import Image

    try:
        img = Image.open(BytesIO(anchor_jpeg))
        img = img.convert("RGB")
        w, h = img.size
        side = max(w, h)
        if side <= ANCHOR_MAX_PX:
            return anchor_jpeg
        scale = ANCHOR_MAX_PX / side
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Anchor resize skipped: %s", exc)
        return anchor_jpeg


async def _call_image_edit(*, prompt: str, anchor_jpeg: bytes) -> bytes | None:
    api_key = _image_api_key()
    if not api_key:
        return None

    prepared = _prepare_anchor_jpeg(anchor_jpeg)
    b64 = base64.b64encode(prepared).decode("ascii")
    payload = {
        "model": _image_model(),
        "prompt": prompt.strip(),
        "image": {"url": f"data:image/jpeg;base64,{b64}", "type": "image_url"},
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                XAI_IMAGE_EDIT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
                logger.warning(
                    "Imagine API %s model=%s: %s",
                    response.status_code,
                    _image_model(),
                    response.text[:400],
                )
                return None
            data = response.json()
            items = data.get("data") or []
            if not items:
                return None
            item = items[0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"])
            url = item.get("url")
            if url:
                img_resp = await client.get(url)
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as exc:
        logger.warning("Imagine edit failed: %s", exc)
    return None


async def generate_scene_portrait(
    anchor_jpeg: bytes,
    *,
    identity_brief: str,
    scene: str,
) -> bytes | None:
    brief = (identity_brief or "same woman, AI chief officer").strip()[:120]
    prompt = (
        f"Same person — face unchanged. {brief}. Setting: {scene.strip()[:200]}. "
        "Square profile photo, photorealistic."
    )
    return await _call_image_edit(prompt=prompt, anchor_jpeg=anchor_jpeg)


async def generate_style_portrait(
    anchor_jpeg: bytes,
    *,
    identity_brief: str,
    style: str,
) -> bytes | None:
    brief = (identity_brief or "same woman, AI chief officer").strip()[:120]
    prompt = (
        f"Same person — face unchanged. {brief}. Style only: {style.strip()[:280]}. "
        "Square profile photo, photorealistic."
    )
    return await _call_image_edit(prompt=prompt, anchor_jpeg=anchor_jpeg)


async def generate_scene_from_anchor_file(
    anchor_path: Path,
    *,
    identity_brief: str,
    scene: str,
) -> bytes | None:
    if not anchor_path.is_file():
        return None
    return await generate_scene_portrait(
        anchor_path.read_bytes(),
        identity_brief=identity_brief,
        scene=scene,
    )


async def generate_style_from_anchor_file(
    anchor_path: Path,
    *,
    identity_brief: str,
    style: str,
) -> bytes | None:
    if not anchor_path.is_file():
        return None
    return await generate_style_portrait(
        anchor_path.read_bytes(),
        identity_brief=identity_brief,
        style=style,
    )


async def generate_banner_portrait(
    anchor_jpeg: bytes,
    *,
    identity_brief: str,
    scene: str,
) -> bytes | None:
    brief = (identity_brief or "same woman, AI chief officer").strip()[:120]
    prompt = (
        f"Same person — face unchanged. {brief}. "
        f"Wide X/Twitter header 3:1. {scene.strip()[:200]}. "
        "No text, photorealistic, dark gold crypto brand."
    )
    return await _call_image_edit(prompt=prompt, anchor_jpeg=anchor_jpeg)