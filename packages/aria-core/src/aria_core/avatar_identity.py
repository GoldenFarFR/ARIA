"""Identité visuelle persistante ARIA — même personnage, décors variés."""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.avatar import (
    _commit_avatar,
    _load_meta,
    _normalize_jpeg,
    _save_meta,
    apply_avatar_sync,
    current_avatar_path,
    format_avatar_sync_status,
)
from aria_core.paths import aria_avatar_dir

logger = logging.getLogger(__name__)

IDENTITY_ANCHOR_NAME = "identity_anchor.jpg"


def identity_anchor_path() -> Path:
    return aria_avatar_dir() / IDENTITY_ANCHOR_NAME


def _identity_block() -> dict[str, Any]:
    meta = _load_meta()
    identity = meta.get("identity")
    if not isinstance(identity, dict):
        return {}
    return identity


def _set_identity_block(identity: dict[str, Any]) -> None:
    meta = _load_meta()
    meta["identity"] = identity
    _save_meta(meta)


def has_identity_anchor() -> bool:
    return identity_anchor_path().is_file()


def is_identity_locked() -> bool:
    block = _identity_block()
    return bool(block.get("locked")) and has_identity_anchor()


def is_pending_identity_anchor() -> bool:
    return bool(_identity_block().get("pending_anchor"))


def set_pending_identity_anchor(pending: bool = True) -> None:
    block = _identity_block()
    block["pending_anchor"] = pending
    _set_identity_block(block)


def get_identity_status() -> dict[str, Any]:
    block = _identity_block()
    return {
        "has_anchor": has_identity_anchor(),
        "locked": is_identity_locked(),
        "pending_anchor": is_pending_identity_anchor(),
        "brief": block.get("brief") or "",
        "anchor_at": block.get("anchor_at"),
        "scenes": block.get("scenes") or [],
    }


async def _extract_identity_brief(image_jpeg: bytes) -> str:
    from aria_core.llm_vision import vision_analyze

    raw = await vision_analyze(
        image_jpeg,
        "Décris ce personnage pour référence future (visage, cheveux, âge apparent, "
        "style, tenue). 3-4 phrases en français. Pas de décor — identité seulement.",
    )
    if raw:
        return raw.strip()[:800]
    return "Personnage ARIA — référence opérateur (brief vision indisponible)"


async def _verify_same_person(anchor_jpeg: bytes, new_jpeg: bytes) -> tuple[bool, str]:
    from aria_core.llm_vision import vision_analyze

    instruction = (
        "Est-ce la MÊME personne que la référence identité ci-dessus "
        "(même visage reconnaissable, traits cohérents) ?\n"
        "Réponds EXACTEMENT une ligne : OUI ou NON puis une courte raison."
    )
    brief = await _extract_identity_brief(anchor_jpeg)
    raw = await vision_analyze(
        new_jpeg,
        f"Référence identité attendue :\n{brief}\n\n{instruction}",
    )
    if not raw:
        return True, "vérification vision indisponible — upload accepté"
    upper = raw.upper()
    if upper.startswith("NON") or "NOT THE SAME" in upper:
        return False, raw
    if upper.startswith("OUI") or "SAME PERSON" in upper:
        return True, raw
    return True, raw


async def establish_identity_anchor(
    data: bytes,
    *,
    source: str,
    note: str = "",
) -> dict[str, Any]:
    """Enregistre l'ancre identité + photo de profil courante."""
    normalized = _normalize_jpeg(data)
    anchor = identity_anchor_path()
    anchor.write_bytes(normalized)

    brief = await _extract_identity_brief(normalized)
    block = _identity_block()
    block.update(
        {
            "locked": True,
            "pending_anchor": False,
            "brief": brief,
            "anchor_at": datetime.now(timezone.utc).isoformat(),
            "anchor_source": source,
            "scenes": block.get("scenes") or [],
        }
    )
    _set_identity_block(block)

    tmp = aria_avatar_dir() / "_identity_upload.jpg"
    tmp.write_bytes(normalized)
    try:
        entry = _commit_avatar(
            tmp,
            source=source,
            note=note or "Identité visuelle établie — ancre opérateur",
        )
        entry["identity"] = {"established": True, "brief": brief[:200]}
        entry["sync"] = await apply_avatar_sync()
        meta = _load_meta()
        meta["current"] = entry
        _save_meta(meta)
        return entry
    finally:
        tmp.unlink(missing_ok=True)


async def set_profile_with_identity(
    data: bytes,
    *,
    source: str,
    note: str = "",
    force_establish: bool = False,
) -> dict[str, Any]:
    """
    Upload profil avec règles identité :
    - pas d'ancre ou force_establish → établit l'ancre
    - ancre existante → vérifie même personnage puis met à jour le profil
    """
    normalized = _normalize_jpeg(data)

    if force_establish or not has_identity_anchor() or is_pending_identity_anchor():
        return await establish_identity_anchor(data, source=source, note=note)

    anchor_bytes = identity_anchor_path().read_bytes()
    ok, reason = await _verify_same_person(anchor_bytes, normalized)
    if not ok:
        raise ValueError(
            "Cette photo ne correspond pas à l'identité ARIA établie. "
            f"{reason[:300]}\n"
            "Envoie une photo du même personnage, ou /avatar identity reset pour recommencer."
        )

    tmp = aria_avatar_dir() / "_identity_upload.jpg"
    tmp.write_bytes(normalized)
    try:
        entry = _commit_avatar(tmp, source=source, note=note)
        entry["identity"] = {"verified": True, "reason": reason[:200]}
        entry["sync"] = await apply_avatar_sync()
        meta = _load_meta()
        meta["current"] = entry
        _save_meta(meta)
        return entry
    finally:
        tmp.unlink(missing_ok=True)


async def apply_scene_portrait(scene: str) -> dict[str, Any]:
    """Génère un nouveau portrait (même personnage, nouveau décor) depuis l'ancre."""
    if not has_identity_anchor():
        raise RuntimeError("Aucune ancre identité — envoie d'abord ta photo de référence.")

    from aria_core.portrait_scene import generate_scene_from_anchor_file

    block = _identity_block()
    brief = block.get("brief") or ""
    generated = await generate_scene_from_anchor_file(
        identity_anchor_path(),
        identity_brief=brief,
        scene=scene,
    )
    if not generated:
        raise RuntimeError(
            "Génération scène indisponible (clé xAI requise : LLM_PROVIDER=xai ou IMAGE_API_KEY). "
            "Sinon envoie une photo du même personnage sur le lieu voulu."
        )

    entry = await set_profile_with_identity(
        generated,
        source=f"scene:{scene[:80]}",
        note=f"Scène : {scene[:200]}",
    )
    scenes = list(block.get("scenes") or [])
    scenes.insert(0, {"scene": scene[:200], "at": datetime.now(timezone.utc).isoformat()})
    block["scenes"] = scenes[:15]
    _set_identity_block(block)
    return entry


def reset_identity_anchor() -> None:
    """Supprime l'ancre — prochaine photo réétablira l'identité."""
    if identity_anchor_path().exists():
        identity_anchor_path().unlink()
    block = _identity_block()
    block.update(
        {
            "locked": False,
            "pending_anchor": True,
            "brief": "",
            "anchor_at": None,
            "scenes": [],
        }
    )
    _set_identity_block(block)


def copy_current_as_anchor() -> bool:
    """Copie current.jpg vers ancre (sans vision)."""
    cur = current_avatar_path()
    if not cur.is_file():
        return False
    shutil.copy2(cur, identity_anchor_path())
    block = _identity_block()
    block.update({"locked": True, "pending_anchor": False})
    _set_identity_block(block)
    return True


def ensure_identity_anchor_from_current() -> bool:
    """
    Si avatar profil (current.jpg) existe mais pas d'ancre — copie vers identity_anchor.jpg.

    Ceci verrouille la **référence visage** pour générer bannière 3:1 ou scènes ;
    ce n'est **pas** la bannière X ni un remplacement de /avatar identity opérateur.
    """
    if has_identity_anchor():
        return True
    if copy_current_as_anchor():
        logger.info("identity anchor seeded from current.jpg")
        return True
    return False


def format_identity_status() -> str:
    st = get_identity_status()
    lines = ["Identité visuelle ARIA"]
    if st["has_anchor"]:
        lines.append("Ancre : ✅ établie")
        if st["brief"]:
            lines.append(f"Référence : {st['brief'][:280]}")
    elif st["pending_anchor"]:
        lines.append("En attente : envoie TA photo de référence (légende /avatar)")
    else:
        lines.append("Pas encore d'ancre — /avatar identity pour verrouiller")
    if st["scenes"]:
        last = st["scenes"][0].get("scene", "")
        if last:
            lines.append(f"Dernière scène : {last[:120]}")
    return "\n".join(lines)


def caption_requests_identity(caption: str) -> bool:
    lower = (caption or "").lower()
    return bool(re.search(r"identit|ancre|reference|référence|identity", lower))