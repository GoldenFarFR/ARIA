"""Rafraîchissement périodique du style avatar ARIA — Grok Imagine (image_edit)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.avatar import _load_meta, _normalize_jpeg
from aria_core.avatar_identity import (
    has_identity_anchor,
    identity_anchor_path,
    set_profile_with_identity,
)
from aria_core.paths import aria_avatar_dir
from aria_core.portrait_scene import _image_api_key, generate_style_from_anchor_file

logger = logging.getLogger(__name__)

STATE_NAME = "style_refresh.json"
PENDING_PREVIEW = "style_pending.jpg"
ALLOWED_INTERVALS = (14,)

# Presets locaux — 0 token Groq avant chaque Imagine
STYLE_PRESETS = (
    "Lumière studio douce, accents or ZHC, fond sombre minimal — autorité calme.",
    "Palette violet holding, rim light, élégance crypto-fondateur.",
    "Cinématique or et noir, bokeh discret, tenue business moderne.",
    "Lumière dorée latérale, contraste doux, premium dark brand.",
    "Ambiance néon subtil violet-or, regard confiant, photo pro.",
)


def _state_path() -> Path:
    return aria_avatar_dir() / STATE_NAME


def _pending_path() -> Path:
    return aria_avatar_dir() / PENDING_PREVIEW


def _default_state() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_days": 14,
        "last_run_at": None,
        "next_due_at": None,
        "pending": None,
        "history": [],
    }


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        base = _default_state()
        base.update(data)
        return base
    except Exception:
        return _default_state()


def _save_state(state: dict[str, Any]) -> None:
    aria_avatar_dir().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _interval_days() -> int:
    from aria_core.runtime import settings

    env = os.environ.get("ARIA_AVATAR_STYLE_INTERVAL_DAYS", "").strip()
    if env.isdigit():
        days = int(env)
        if days in ALLOWED_INTERVALS:
            return days
    state = _load_state()
    days = state.get("interval_days")
    if days in ALLOWED_INTERVALS:
        return int(days)
    cfg = int(getattr(settings, "aria_avatar_style_interval_days", 0) or 0)
    if cfg in ALLOWED_INTERVALS:
        return cfg
    return 14


def _enabled() -> bool:
    from aria_core.runtime import settings

    if not getattr(settings, "aria_avatar_style_enabled", True):
        return False
    env = os.environ.get("ARIA_AVATAR_STYLE_ENABLED", "").strip().lower()
    if env:
        return env in ("1", "true", "yes", "on")
    return bool(_load_state().get("enabled", True))


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_next_due(from_dt: datetime | None = None) -> str:
    base = from_dt or datetime.now(timezone.utc)
    return (base + timedelta(days=_interval_days())).isoformat()


def is_image_generation_available() -> bool:
    return bool(_image_api_key())


def update_config(
    *,
    enabled: bool | None = None,
    interval_days: int | None = None,
) -> dict[str, Any]:
    state = _load_state()
    if enabled is not None:
        state["enabled"] = enabled
    if interval_days is not None:
        if interval_days not in ALLOWED_INTERVALS:
            raise ValueError(f"interval_days doit être 14 (reçu {interval_days})")
        state["interval_days"] = interval_days
    _save_state(state)
    return get_refresh_status()


def is_due() -> bool:
    if not _enabled():
        return False
    state = _load_state()
    if state.get("pending"):
        return False
    due = _parse_dt(state.get("next_due_at"))
    if due is None:
        return True
    return datetime.now(timezone.utc) >= due


def get_refresh_status() -> dict[str, Any]:
    state = _load_state()
    pending = state.get("pending")
    return {
        "enabled": _enabled(),
        "interval_days": _interval_days(),
        "image_api_configured": is_image_generation_available(),
        "identity_anchor": has_identity_anchor(),
        "last_run_at": state.get("last_run_at"),
        "next_due_at": state.get("next_due_at"),
        "is_due": is_due(),
        "has_pending": bool(pending),
        "pending": pending,
        "history": (state.get("history") or [])[:8],
    }


def _style_use_llm() -> bool:
    env = os.environ.get("ARIA_IMAGE_STYLE_USE_LLM", "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    from aria_core.runtime import settings

    return bool(getattr(settings, "aria_image_style_use_llm", False))


def _pick_style_preset(state: dict[str, Any]) -> str:
    history = state.get("history") or []
    used = {str(h.get("style_prompt", ""))[:80] for h in history[:5]}
    for preset in STYLE_PRESETS:
        if preset not in used:
            return preset
    return STYLE_PRESETS[len(history) % len(STYLE_PRESETS)]


async def propose_style(*, force_new: bool = False) -> str:
    """Aria propose un nouveau style visuel (texte, pas encore d'image)."""
    state = _load_state()
    pending = state.get("pending") or {}
    if pending.get("style_prompt") and not force_new:
        return str(pending["style_prompt"])

    if not _style_use_llm():
        return _pick_style_preset(state)

    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.narrative import llm_system_block

    history = state.get("history") or []
    recent = ", ".join(h.get("style_label", "")[:40] for h in history[:3] if h.get("style_label"))

    if not is_llm_configured():
        return _pick_style_preset(state)

    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        "Tu choisis UN nouveau style visuel pour ta photo de profil (Grok Imagine). "
        "Même visage, nouveau mood : lumière, palette, tenue légère, ambiance. "
        "Pas de lieu exotique — style photo pro. 2-3 phrases en français. "
        "Évite de répéter les styles récents."
    )
    user = "Propose le prochain style avatar ARIA."
    if recent:
        user += f"\nStyles récents à éviter : {recent}"

    raw = await chat_with_context(user, system, temperature=0.65, max_tokens=220)
    style = (raw or "").strip()
    if not style:
        style = (
            "Lumière studio douce, accents violet ZHC, fond dégradé minimal, "
            "tenue business moderne — autorité calme."
        )
    return style[:600]


async def generate_pending_style(style: str | None = None) -> dict[str, Any]:
    """Génère un aperçu (non appliqué) depuis l'ancre identité."""
    if not has_identity_anchor():
        raise RuntimeError("Ancre identité manquante — /avatar identity d'abord.")
    if not is_image_generation_available():
        raise RuntimeError(
            "Grok Imagine indisponible — configure IMAGE_API_KEY ou LLM_PROVIDER=xai."
        )

    meta = _load_meta()
    identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
    brief = identity.get("brief") or ""

    style_prompt = (style or await propose_style(force_new=True)).strip()
    generated = await generate_style_from_anchor_file(
        identity_anchor_path(),
        identity_brief=brief,
        style=style_prompt,
    )
    if not generated:
        raise RuntimeError("Génération Grok Imagine échouée — réessaie plus tard.")

    normalized = _normalize_jpeg(generated)
    _pending_path().write_bytes(normalized)

    state = _load_state()
    label = style_prompt.split(".")[0][:120]
    pending = {
        "style_prompt": style_prompt,
        "style_label": label,
        "generated_at": _iso_now(),
        "preview_file": PENDING_PREVIEW,
    }
    state["pending"] = pending
    _save_state(state)
    return {"ok": True, "pending": pending}


async def apply_pending_style(*, note: str = "") -> dict[str, Any]:
    """Applique l'aperçu en attente + sync Telegram/X."""
    state = _load_state()
    pending = state.get("pending")
    if not pending or not _pending_path().is_file():
        raise RuntimeError("Aucun style en attente — génère d'abord (/avatar style now).")

    data = _pending_path().read_bytes()
    style_label = pending.get("style_label") or "Grok Imagine"
    entry = await set_profile_with_identity(
        data,
        source="style_refresh",
        note=note or f"Style périodique : {style_label[:200]}",
    )

    now = _iso_now()
    hist = state.get("history") or []
    hist.insert(
        0,
        {
            "style_label": style_label,
            "style_prompt": pending.get("style_prompt", "")[:300],
            "applied_at": now,
        },
    )
    state["history"] = hist[:20]
    state["pending"] = None
    state["last_run_at"] = now
    state["next_due_at"] = _compute_next_due()
    _save_state(state)
    _pending_path().unlink(missing_ok=True)

    return {"ok": True, "current": entry, "next_due_at": state["next_due_at"]}


def discard_pending() -> str:
    state = _load_state()
    state["pending"] = None
    _save_state(state)
    _pending_path().unlink(missing_ok=True)
    return "Aperçu style refusé — prochain cycle au prochain échéance."


async def run_refresh_cycle(
    *, notify: bool = True, auto_apply: bool | None = None, force: bool = False,
) -> dict[str, Any]:
    """
    Cycle planifié : propose + génère style Imagine.
    auto_apply=True (mode ZHC) : publie directement Telegram/X sans validation.
    """
    from aria_core.visual_autonomy import visual_auto_apply_enabled

    if auto_apply is None:
        auto_apply = visual_auto_apply_enabled()

    if not _enabled():
        return {"skipped": True, "reason": "disabled"}
    if not force and not is_due():
        return {"skipped": True, "reason": "not_due"}
    if not has_identity_anchor():
        return {"skipped": True, "reason": "no_identity_anchor"}
    if not is_image_generation_available():
        return {"skipped": True, "reason": "no_image_api"}

    try:
        result = await generate_pending_style()
    except Exception as exc:
        logger.warning("Avatar style refresh failed: %s", exc)
        return {"skipped": True, "reason": str(exc)[:200]}

    pending = result.get("pending") or {}
    if auto_apply:
        try:
            applied = await apply_pending_style(note="Cycle visuel autonome — Grok Imagine")
        except Exception as exc:
            logger.warning("Avatar auto-apply failed: %s", exc)
            return {"skipped": True, "reason": str(exc)[:200]}
        if notify:
            await _notify_operator_applied(applied)
        return {"ok": True, "applied": True, "current": applied.get("current"), "pending": pending}

    if notify:
        await _notify_operator_pending(pending)

    return {"ok": True, "pending": pending, "notified": notify}


async def _notify_operator_applied(applied: dict[str, Any]) -> None:
    try:
        from aria_core.gateway.telegram_bot import send_message

        cur = applied.get("current") or {}
        note = cur.get("note", "")
        await send_message(
            "🎨 Style avatar appliqué (Grok Imagine, mode autonome)\n\n"
            f"{note[:280]}\n\n"
            "Sync Telegram + X effectuée."
        )
    except Exception as exc:
        logger.warning("Style apply notify failed: %s", exc)


async def _notify_operator_pending(pending: dict[str, Any]) -> None:
    try:
        from aria_core.gateway.telegram_bot import send_message, send_photo

        label = pending.get("style_label") or "nouveau style"
        prompt = pending.get("style_prompt") or ""
        interval = _interval_days()
        text = (
            "🎨 Nouveau style avatar (Grok Imagine)\n\n"
            f"Style : {label}\n\n"
            f"{prompt[:500]}\n\n"
            "Aperçu ci-dessous — rien n'est publié tant que tu ne valides pas.\n"
            "/avatar style apply — appliquer + sync Telegram/X\n"
            "/avatar style skip — refuser\n\n"
            f"Prochain cycle auto : {interval} jours après validation."
        )
        await send_message(text)
        if _pending_path().is_file():
            await send_photo(_pending_path(), caption="Aperçu style ARIA")
    except Exception as exc:
        logger.warning("Style refresh notify failed: %s", exc)


def pending_preview_path() -> Path | None:
    path = _pending_path()
    return path if path.is_file() else None


def format_refresh_status() -> str:
    st = get_refresh_status()
    lines = [
        "Style avatar périodique (Grok Imagine)",
        f"Actif : {'oui' if st['enabled'] else 'non'} · tous les {st['interval_days']} jours",
        f"Imagine : {'✅' if st['image_api_configured'] else '❌ IMAGE_API_KEY ou xAI'}",
        f"Ancre identité : {'✅' if st['identity_anchor'] else '❌ /avatar identity'}",
    ]
    if st.get("next_due_at"):
        lines.append(f"Prochain cycle : {st['next_due_at'][:16].replace('T', ' ')} UTC")
    if st.get("has_pending"):
        p = st.get("pending") or {}
        lines.append(f"En attente : {p.get('style_label', 'aperçu')[:80]}")
        lines.append("/avatar style apply · /avatar style skip")
    elif st.get("is_due"):
        lines.append("Échéance atteinte — /avatar style now pour générer")
    if st.get("history"):
        last = st["history"][0]
        lines.append(f"Dernier appliqué : {last.get('style_label', '')[:80]}")
    return "\n".join(lines)