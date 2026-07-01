"""Auto-maintenance ARIA — ordres operateur, curiosite, action (pas seulement repondre)."""

from __future__ import annotations

import logging
from typing import Any

from aria_core.memory import append_memory
from aria_core.operator_self_directive import (
    SelfMaintenanceAction,
    classify_operator_message,
    parse_self_maintenance_action,
    OperatorMessageKind,
)

logger = logging.getLogger(__name__)


def _log_step(step: str, detail: str = "") -> None:
    line = f"[self-maintenance] {step}"
    if detail:
        line += f" — {detail[:300]}"
    append_memory("identity", line)
    logger.info(line)


async def run_curiosity_x_banner_cycle(*, lang: str = "fr") -> str:
    """
    Boucle curiosite : observe -> gap -> capacite -> agit -> rapport.
    """
    from aria_core.identity import official_x_at
    from aria_core.x_banner import (
        ensure_x_banner_file,
        format_visual_assets_lines,
        get_visual_assets_status,
        get_x_banner_status,
    )

    _log_step("curiosity_start", "banniere X")
    status = await get_x_banner_status()
    assets = await get_visual_assets_status()
    has_remote = bool(status.get("has_banner"))
    has_local = bool(status.get("local_banner"))
    x_ok = bool(status.get("x_configured"))

    steps: list[str] = []
    if lang == "fr":
        steps.append("Curiosite — banniere X @Aria_ZHC (header 3:1, ≠ avatar carre)")
        steps.append("")
        steps.append("1. Observation — 3 actifs distincts")
        steps.extend(format_visual_assets_lines(lang="fr"))
        steps.append(f"   Banniere publiee sur X : {'oui' if has_remote else 'non'}")
        steps.append(f"   API X configuree : {'oui' if x_ok else 'non'}")
    else:
        steps.append("Curiosity — X banner @Aria_ZHC (3:1 header, ≠ square avatar)")
        steps.append("")
        steps.append("1. Observe — avatar / anchor / banner are separate")
        steps.extend(format_visual_assets_lines(lang="en"))
        steps.append(f"   Banner live on X: {has_remote}, API: {x_ok}")

    if has_remote and not has_local:
        _log_step("gap", "banniere X presente mais pas de copie locale")
        steps.append("2. Gap — banniere en ligne sans asset local (sync a prevu)")

    if not has_remote:
        _log_step("gap", "pas de banniere X — je peux en generer une")
        steps.append("2. Gap — pas de banniere sur mon profil X")

    steps.append("3. Capacite — update_profile_banner + generate_banner_portrait")

    if not x_ok:
        from aria_core.capability_gap import file_capability_gap, format_gap_reply

        gap = await file_capability_gap(
            "x_oauth_write",
            context="run_curiosity_x_banner_cycle: X OAuth non configure",
            lang=lang,
        )
        _log_step("blocked", "x oauth — cap-gap filed")
        msg = (
            "\n".join(steps)
            + "\n\n4. Action bloquee — cles X OAuth manquantes (Read+Write).\n"
            + format_gap_reply(gap, lang=lang)
        )
        return msg

    from aria_core.avatar_identity import ensure_identity_anchor_from_current, has_identity_anchor
    from aria_core.portrait_scene import _image_api_key

    ensure_identity_anchor_from_current()

    if not has_identity_anchor():
        from aria_core.capability_gap import file_capability_gap, format_gap_reply

        gap = await file_capability_gap(
            "identity_anchor",
            context="run_curiosity_x_banner_cycle: anchor identite absent",
            lang=lang,
        )
        _log_step("blocked", "identity anchor — cap-gap filed")
        msg = (
            "\n".join(steps)
            + "\n\n4. Action bloquee — ancre identite (reference visage) absente.\n"
            + "   Ce n'est pas la banniere : l'avatar profil peut exister sans ancre.\n"
            + "   Telegram : /avatar identity — puis ARIA genere x_banner.jpg (3:1) separement.\n"
            + format_gap_reply(gap, lang=lang)
        )
        return msg

    if not _image_api_key():
        from aria_core.capability_gap import file_capability_gap, format_gap_reply

        gap = await file_capability_gap(
            "image_api_key",
            context=(
                "run_curiosity_x_banner_cycle: IMAGE_API_KEY absente "
                "(cle xAI Imagine — requise si LLM_PROVIDER=groq)"
            ),
            lang=lang,
        )
        _log_step("blocked", "image api key — cap-gap filed")
        msg = (
            "\n".join(steps)
            + "\n\n4. Action bloquee — cle generation banniere 3:1 (pas l'avatar).\n"
            + "   Render : IMAGE_API_KEY=xai-... dans production.env puis sync-render.\n"
            + format_gap_reply(gap, lang=lang)
        )
        return msg

    path = await ensure_x_banner_file()
    if not path:
        from aria_core.capability_gap import file_capability_gap, format_gap_reply

        gap = await file_capability_gap(
            "x_banner_generate",
            context=(
                "generate_x_banner_jpeg vide — ancre OK mais x_banner.jpg absent. "
                f"assets={assets}"
            ),
            lang=lang,
        )
        _log_step("blocked", "banner gen failed — cap-gap filed")
        msg = (
            "\n".join(steps)
            + "\n\n4. Action bloquee — generation banniere X 3:1 impossible (API/quota).\n"
            + "   L'avatar profil (current.jpg) n'est pas affecte.\n"
            + format_gap_reply(gap, lang=lang)
        )
        return msg

    from aria_core.gateway.x_twitter import apply_profile_banner

    ok = await apply_profile_banner(path)
    if ok:
        _log_step("action_done", f"banniere mise a jour {path.name}")
        steps.append("4. Action — banniere generee et publiee sur X")
        steps.append(f"   Fichier : {path}")
        steps.append(f"   Profil : {official_x_at()}")
        steps.append("")
        steps.append("Preuve : verifie sur X — la banniere doit etre visible.")
        return "\n".join(steps)

    from aria_core.capability_gap import file_capability_gap, format_gap_reply

    gap = await file_capability_gap(
        "x_profile_banner",
        context="apply_profile_banner a retourne False",
        lang=lang,
    )
    _log_step("action_failed", "apply_profile_banner — cap-gap filed")
    steps.append("4. Echec upload X — voir logs Render")
    steps.append(format_gap_reply(gap, lang=lang))
    return "\n".join(steps)


async def execute_self_maintenance(action: SelfMaintenanceAction, *, lang: str = "fr") -> str:
    if action in (SelfMaintenanceAction.UPDATE_X_BANNER, SelfMaintenanceAction.CURIOSITY_X_BANNER):
        return await run_curiosity_x_banner_cycle(lang=lang)

    if action == SelfMaintenanceAction.UPDATE_X_AVATAR:
        from aria_core.avatar import aria_choose_avatar, apply_avatar_sync, format_avatar_sync_status, get_avatar_status

        _log_step("directive", "update avatar")
        pick_id = await aria_choose_avatar()
        sync = await apply_avatar_sync()
        status = get_avatar_status()
        note = (status.get("current") or {}).get("note", "")
        return (
            f"Photo de profil mise a jour : {pick_id}\n{note}\n"
            f"{format_avatar_sync_status(sync)}"
        )

    return "Action non reconnue."


async def handle_operator_self_message(message: str, *, lang: str = "fr") -> str | None:
    """
    Intercepte ordres a ARIA elle-meme (admin). None = laisser brain traiter.
    """
    kind = classify_operator_message(message)
    if kind not in (OperatorMessageKind.SELF_DIRECTIVE, OperatorMessageKind.CURIOSITY_GAP):
        return None

    action = parse_self_maintenance_action(message)
    if not action:
        return None

    _log_step("operator_message", f"{kind.value} -> {action.value}")
    try:
        return await execute_self_maintenance(action, lang=lang)
    except Exception as exc:
        logger.exception("self_maintenance failed")
        return f"Echec auto-maintenance ({action.value}) : {exc.__class__.__name__}: {exc}"


def self_maintenance_context_for_brain() -> str:
    """Bloc memoire pour eviter re-routage ACTU sur ordres operateur."""
    return (
        "## [Operator self-directive]\n"
        "Si le message parle de TA/TON profil X, banniere ou avatar ARIA : "
        "executer self_maintenance, pas de recherche web ACTU.\n"
        "Curiosite : observer gap, verifier capacite, agir, rapporter preuve."
    )