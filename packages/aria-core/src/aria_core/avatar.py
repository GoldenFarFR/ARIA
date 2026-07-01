"""ARIA profile avatar — operator-managed, self-service pick/upload, Telegram sync."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from aria_core.paths import aria_avatar_dir, aria_avatar_gallery_dir

logger = logging.getLogger(__name__)

CURRENT_NAME = "current.jpg"
META_NAME = "meta.json"
GALLERY_VARIANTS = (
    ("zhc-gold", "ARIA ZHC — or holding", (212, 175, 55), "Luxe or, holding mère ZHC, autorité calme et chaleureuse"),
    ("zhc-violet", "ARIA — futur violet", (124, 58, 237), "Vision crypto, intelligence, élégance futuriste"),
    ("zhc-onyx", "ZHC — onyx minimal", (15, 23, 42), "Discrétion, rigueur, exécution silencieuse"),
)


def _meta_path() -> Path:
    return aria_avatar_dir() / META_NAME


def current_avatar_path() -> Path:
    return aria_avatar_dir() / CURRENT_NAME


def _load_meta() -> dict[str, Any]:
    path = _meta_path()
    if not path.exists():
        return {"history": [], "current": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"history": [], "current": None}


def _save_meta(meta: dict[str, Any]) -> None:
    _meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _gallery_path(name: str) -> Path:
    safe = "".join(c for c in name.lower() if c.isalnum() or c in "-_")
    return aria_avatar_gallery_dir() / f"{safe}.jpg"


def _bundled_gallery_asset(slug: str) -> Path | None:
    bundled = Path(__file__).resolve().parent / "assets" / "avatar_gallery" / f"{slug}.jpg"
    return bundled if bundled.is_file() else None


def _render_variant(label: str, rgb: tuple[int, int, int], out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    size = 640
    img = Image.new("RGB", (size, size), rgb)
    draw = ImageDraw.Draw(img)
    margin = 48
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=40,
        outline=(255, 255, 255),
        width=6,
    )
    try:
        font = ImageFont.truetype("arial.ttf", 72)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2), label, fill=(255, 255, 255), font=font)
    img.save(out, format="JPEG", quality=92)


def ensure_gallery_seeded() -> list[str]:
    names: list[str] = []
    for slug, label, color, _desc in GALLERY_VARIANTS:
        path = _gallery_path(slug)
        if not path.exists():
            bundled = _bundled_gallery_asset(slug)
            if bundled:
                shutil.copy2(bundled, path)
            else:
                _render_variant(label, color, path)
        names.append(slug)
    return names


def _normalize_jpeg(data: bytes) -> bytes:
    from PIL import Image

    img = Image.open(BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((640, 640), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _commit_avatar(source_path: Path, *, source: str, note: str = "") -> dict[str, Any]:
    dest = current_avatar_path()
    shutil.copy2(source_path, dest)
    entry = {
        "source": source,
        "note": note[:240],
        "file": CURRENT_NAME,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    meta = _load_meta()
    meta["current"] = entry
    history = meta.get("history") or []
    history.insert(0, entry)
    meta["history"] = history[:20]
    _save_meta(meta)
    return entry


def list_gallery() -> list[dict[str, str]]:
    ensure_gallery_seeded()
    items: list[dict[str, str]] = []
    for slug, label, _, desc in GALLERY_VARIANTS:
        path = _gallery_path(slug)
        if path.exists():
            items.append({"id": slug, "label": label, "description": desc})
    return items


def get_avatar_status() -> dict[str, Any]:
    meta = _load_meta()
    current = meta.get("current")
    path = current_avatar_path()
    from aria_core.avatar_identity import get_identity_status
    from aria_core.gateway.x_twitter import is_x_post_configured
    from aria_core.identity import official_x_handle

    return {
        "has_avatar": path.exists(),
        "current": current,
        "gallery": list_gallery(),
        "identity": get_identity_status(),
        "public_url": "/api/aria/avatar",
        "sync_targets": {
            "telegram": "bot profile (@Aria_ZHC_Bot)",
            "x": f"@{official_x_handle()}" if is_x_post_configured() else None,
        },
    }


async def _finalize_avatar_entry(entry: dict[str, Any]) -> dict[str, Any]:
    entry["sync"] = await apply_avatar_sync()
    meta = _load_meta()
    meta["current"] = entry
    _save_meta(meta)
    return entry


async def pick_gallery_avatar(avatar_id: str, *, note: str = "") -> dict[str, Any]:
    ensure_gallery_seeded()
    path = _gallery_path(avatar_id)
    if not path.exists():
        raise FileNotFoundError(avatar_id)
    entry = _commit_avatar(path, source=f"gallery:{avatar_id}", note=note or f"Picked {avatar_id}")
    return await _finalize_avatar_entry(entry)


async def set_avatar_from_bytes(data: bytes, *, source: str, note: str = "") -> dict[str, Any]:
    normalized = _normalize_jpeg(data)
    tmp = aria_avatar_dir() / "_upload.jpg"
    tmp.write_bytes(normalized)
    try:
        entry = _commit_avatar(tmp, source=source, note=note)
        return await _finalize_avatar_entry(entry)
    finally:
        tmp.unlink(missing_ok=True)


def format_avatar_sync_status(sync: dict[str, Any]) -> str:
    """Human-readable sync line for Telegram replies."""
    tg = sync.get("telegram")
    x = sync.get("x")
    tg_mark = "✅" if tg else "—"
    x_mark = "✅" if x else "—"
    line = f"Telegram {tg_mark} · X {x_mark}"
    errors = sync.get("errors") or {}
    notes: list[str] = []
    if not tg and errors.get("telegram"):
        notes.append(f"Telegram : {errors['telegram']}")
    if not x and errors.get("x"):
        notes.append(f"X : {errors['x']}")
    if notes:
        line += "\n" + "\n".join(notes)
    return line


async def apply_telegram_avatar() -> tuple[bool, str | None]:
    path = current_avatar_path()
    if not path.exists():
        return False, "aucune image locale"
    try:
        from aria_core.gateway.telegram_bot import apply_bot_profile_photo

        return await apply_bot_profile_photo(path)
    except Exception as exc:
        logger.warning("Telegram avatar sync failed: %s", exc)
        return False, str(exc)


async def apply_x_avatar() -> tuple[bool, str | None]:
    path = current_avatar_path()
    if not path.exists():
        return False, "aucune image locale"
    try:
        from aria_core.gateway.x_twitter import apply_profile_image

        ok = await apply_profile_image(path)
        if ok:
            return True, None
        return False, "API X non configurée ou refusée"
    except Exception as exc:
        logger.warning("X avatar sync failed: %s", exc)
        return False, str(exc)


async def apply_avatar_sync() -> dict[str, Any]:
    """Push current avatar to Telegram bot + X @Aria_ZHC."""
    path = current_avatar_path()
    if not path.exists():
        return {"telegram": False, "x": False, "errors": {}}
    (tg_ok, tg_err), (x_ok, x_err) = await asyncio.gather(
        apply_telegram_avatar(),
        apply_x_avatar(),
    )
    errors: dict[str, str] = {}
    if not tg_ok and tg_err:
        errors["telegram"] = tg_err
    if not x_ok and x_err:
        errors["x"] = x_err
    return {"telegram": bool(tg_ok), "x": bool(x_ok), "errors": errors}


def ensure_avatar_seeded() -> None:
    """Seed gallery and default avatar file if missing (no Telegram API)."""
    ensure_gallery_seeded()
    if not current_avatar_path().exists():
        _commit_avatar(
            _gallery_path("zhc-gold"),
            source="gallery:zhc-gold",
            note="Initial ARIA profile — change anytime via /avatar",
        )


def _parse_avatar_choice(raw: str, valid_ids: set[str]) -> tuple[str | None, str]:
    text = (raw or "").strip()
    note = ""
    for line in text.splitlines():
        lower = line.lower().strip()
        if lower.startswith("note:"):
            note = line.split(":", 1)[-1].strip()[:240]
    for token in text.replace(",", " ").split():
        slug = token.strip().lower().strip("`*_")
        if slug in valid_ids:
            return slug, note
    return None, note


async def _llm_pick_avatar_id() -> tuple[str | None, str]:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block

    gallery = list_gallery()
    if not gallery or not is_llm_configured():
        return None, ""

    options = "\n".join(
        f"- {g['id']}: {g['label']} — {g.get('description', '')}" for g in gallery
    )
    valid = {g["id"] for g in gallery}
    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        "Tu choisis TA propre photo de profil publique (Telegram + X + API). "
        "Réponds avec l'id exact sur la première ligne, puis NOTE: <une phrase en français>."
    )
    user = (
        "Choisis la variante qui te représente le mieux comme Chief Autonomous Officer.\n\n"
        f"Variantes :\n{options}\n\n"
        "Format :\n"
        "zhc-gold\n"
        "NOTE: ..."
    )
    raw = await chat_with_context(user, system, temperature=0.4, max_tokens=200)
    if not raw:
        return None, ""
    pick, note = _parse_avatar_choice(raw, valid)
    return pick, note or "ARIA — choix autonome de photo de profil"


async def aria_choose_avatar() -> str:
    gallery = list_gallery()
    if not gallery:
        raise RuntimeError("gallery empty")

    valid = {g["id"] for g in gallery}
    pick, note = await _llm_pick_avatar_id()
    if not pick:
        pick = "zhc-gold" if "zhc-gold" in valid else gallery[0]["id"]
        note = note or "ARIA — choix par défaut (or holding)"

    await pick_gallery_avatar(pick, note=note)
    return pick


def _is_factory_default_avatar() -> bool:
    current = (_load_meta().get("current") or {})
    note = str(current.get("note", ""))
    return "Initial ARIA profile" in note or not current


async def ensure_avatar_ready() -> None:
    from aria_core.avatar_identity import ensure_identity_anchor_from_current

    ensure_avatar_seeded()
    ensure_identity_anchor_from_current()
    if _is_factory_default_avatar():
        try:
            await aria_choose_avatar()
        except Exception as exc:
            logger.warning("Autonomous avatar choice skipped: %s", exc)
    await apply_avatar_sync()