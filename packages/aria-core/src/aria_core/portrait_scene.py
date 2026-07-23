"""ARIA portrait generation — same character, new setting (optional image API)."""

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
XAI_IMAGE_GENERATIONS_URL = "https://api.x.ai/v1/images/generations"
BANNER_GENERATION_ASPECT = "2:1"  # normalized to 3:1 (1500x500) afterward
# grok-imagine-image = 0.02$/image — quality = 0.05$/image (see docs.x.ai)
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
    """Shrinks the anchor before upload — same Imagine cost, less latency."""
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


async def _extract_image_bytes(client: httpx.AsyncClient, data: dict) -> bytes | None:
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
    return None


async def _call_image_generate(*, prompt: str, aspect_ratio: str = BANNER_GENERATION_ASPECT) -> bytes | None:
    """Text-to-image Imagine — no source photo (brand banner)."""
    api_key = _image_api_key()
    if not api_key:
        return None

    payload = {
        "model": _image_model(),
        "prompt": prompt.strip(),
        "aspect_ratio": aspect_ratio,
        "n": 1,
        "response_format": "b64_json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                XAI_IMAGE_GENERATIONS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code != 200:
                logger.warning(
                    "Imagine generate %s model=%s: %s",
                    response.status_code,
                    _image_model(),
                    response.text[:400],
                )
                return None
            return await _extract_image_bytes(client, response.json())
    except Exception as exc:
        logger.warning("Imagine generate failed: %s", exc)
    return None


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
            return await _extract_image_bytes(client, response.json())
    except Exception as exc:
        logger.warning("Imagine edit failed: %s", exc)
    return None


# Shared descriptors (realism + brand) — a single adjustment point for the 3
# portrait prompts. Explicit operator decision (10/07, AskUserQuestion): aim
# for the technical realism level of references like Lily Turner (skin/light/
# consistency) **without** adopting her register (sexualized content,
# suggestive outfits) — the "never suggestive/sexualized" line below is
# therefore not generic caution, it's the boundary explicitly decided by the
# operator, baked into the prompt itself rather than left to the sole
# discipline of whoever writes the caption.
_REALISM_STYLE = (
    "Shot on a professional DSLR with an 85mm portrait lens, natural skin texture with "
    "visible pores and fine detail, soft directional studio or golden-hour lighting, sharp "
    "focus on the eyes, ultra-realistic photography, no airbrushed or plastic AI look."
)
_BRAND_PRESENCE = (
    "Confident poised expression, direct engaged gaze, commanding presence — someone whose "
    "peers take seriously."
)
_TASTE_BOUNDARY = (
    "Elegant, sophisticated, business-appropriate or editorial styling — never suggestive, "
    "never revealing, never sexualized."
)
# ARIA brand palette (#94) -- anchored on the ONLY brand artifact actually
# shipped (vanguard/src/index.css: near-black surface #08080a, gold
# #c9a962/#e8d5a8, champagne #d4c4a0, cream #f4efe6, Cormorant Garamond
# display serif -- editorial elegance, never futuristic-neon). Before this
# fix, nothing locked the color in these prompts -- the `avatar_style_
# refresh.py` presets had drifted toward a neon-violet absent from the real
# site (fixed in the same commit), one of the most recognizable visual
# cliches of "generic AI" imagery. Locking the palette here, in the SHARED
# prompt, prevents this drift from coming back via a future misaligned
# variant.
_BRAND_PALETTE = (
    "Color palette strictly warm gold and deep charcoal or near-black — the exact ARIA ZHC "
    "brand palette (champagne, warm gold, cream accents on near-black), timeless editorial "
    "luxury. Never neon, never violet or purple, never futuristic sci-fi cyan or blue glow — "
    "those read as generic AI-generated imagery, the opposite of this brand."
)
# The identity brief (vision-model, `_extract_identity_brief`) stays bounded, but is
# less truncated than before (120 -> 220): more detail retained (features, style, usual
# outfit) for more faithful character consistency from one generation to the next.
_IDENTITY_BRIEF_MAX = 220


async def generate_scene_portrait(
    anchor_jpeg: bytes,
    *,
    identity_brief: str,
    scene: str,
) -> bytes | None:
    brief = (identity_brief or "same woman, AI chief officer").strip()[:_IDENTITY_BRIEF_MAX]
    prompt = (
        f"Same person — identical face and facial features unchanged, unmistakably "
        f"recognizable. {brief}. "
        f"New setting and environment: {scene.strip()[:200]}. "
        "Change outfit, hairstyle, background, lighting as needed. "
        f"{_REALISM_STYLE} {_BRAND_PRESENCE} {_BRAND_PALETTE} {_TASTE_BOUNDARY} "
        "Square profile photo."
    )
    return await _call_image_edit(prompt=prompt, anchor_jpeg=anchor_jpeg)


async def generate_style_portrait(
    anchor_jpeg: bytes,
    *,
    identity_brief: str,
    style: str,
) -> bytes | None:
    brief = (identity_brief or "same woman, AI chief officer").strip()[:_IDENTITY_BRIEF_MAX]
    prompt = (
        f"Same person — identical face and facial features unchanged, unmistakably "
        f"recognizable. {brief}. "
        f"Transform everything else: {style.strip()[:280]}. "
        "New outfit, hairstyle, background environment, lighting and mood. "
        f"{_REALISM_STYLE} {_BRAND_PRESENCE} {_BRAND_PALETTE} {_TASTE_BOUNDARY} "
        "Square profile photo."
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
    """Legacy edit-from-anchor — reserved for avatars, not the X banner."""
    brief = (identity_brief or "same woman, AI chief officer").strip()[:_IDENTITY_BRIEF_MAX]
    prompt = (
        f"Same person — face unchanged, unmistakably recognizable. {brief}. "
        f"Wide X/Twitter header 3:1. {scene.strip()[:200]}. "
        f"No text. {_REALISM_STYLE} {_BRAND_PRESENCE} {_BRAND_PALETTE} {_TASTE_BOUNDARY} "
        "Dark gold crypto brand."
    )
    return await _call_image_edit(prompt=prompt, anchor_jpeg=anchor_jpeg)


async def generate_banner_creative(
    *,
    brand_brief: str = "",
    scene: str = "",
) -> bytes | None:
    """X banner 3:1 via Imagine text-to-image — brand mood, no profile photo."""
    brand = (brand_brief or "ARIA ZHC chief AI officer, GoldenFar Zero-Human Company").strip()[:160]
    setting = (scene or "").strip()[:280]
    prompt = (
        f"Wide cinematic X/Twitter profile header background only. Brand mood: {brand}. "
        f"{setting} "
        "Ultra-wide landscape, abstract futuristic fintech aesthetic, deep charcoal and black "
        "with subtle gold and amber light streaks, neural network motifs, holographic market "
        "textures, premium crypto holding atmosphere, editorial quality, "
        "EMPTY SCENE: no people, no faces, no portraits, no human figures, no woman, no man, "
        "no character, no selfies, no profile photo, no avatar, no uniform, no body, "
        "no text, no logos, no watermarks, landscape and atmosphere only."
    )
    return await _call_image_generate(prompt=prompt)