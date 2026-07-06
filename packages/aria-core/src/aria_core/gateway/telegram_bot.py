from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from aria_core.actions import execute_contact_juno, mark_published
from aria_core.approvals import ApprovalStatus, create_approval, get_approval, resolve_approval
from aria_core.brain import aria_brain
from aria_core.exchanges import record_reply
from aria_core.heartbeat import aria_heartbeat
from aria_core.locale import LANG_EN
from aria_core.holding import holding_name
from aria_core.narrative import (
    telegram_admin_start,
    telegram_online_notice,
    telegram_visitor_start,
)
from aria_core.gateway.telegram_format import plain_telegram
from aria_core.identity import (
    fix_handle_in_text,
    official_telegram_bot_url,
    official_telegram_bot_username,
    official_x_url,
)
from aria_core import outgoing_pause
from aria_core.integrations.host_hooks import check_rate_limit
from aria_core.runtime import settings

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

logger = logging.getLogger(__name__)

_bot_app: Application | None = None
_webhook_mode: bool = False
_bot_username: str | None = None


async def get_bot_username() -> str | None:
    """Username Telegram du bot — API ou config TELEGRAM_BOT_USERNAME."""
    global _bot_username
    if _bot_username:
        return _bot_username
    if _bot_app:
        me = await _bot_app.bot.get_me()
        if me.username:
            _bot_username = me.username
            return _bot_username
    configured = official_telegram_bot_username()
    return configured or None


def get_channel_links_text() -> str:
    """Public channel URLs (no @handles)."""
    return f"{official_telegram_bot_url()}\n{official_x_url()}"


def is_running() -> bool:
    return _bot_app is not None


def get_mode() -> str:
    if not _bot_app:
        return "disabled"
    return "webhook" if _webhook_mode else "polling"


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def is_owner(user_id: int) -> bool:
    """Propriétaire unique du kill-switch /stop /start (ARIA_OWNER_CHAT_ID, fallback admin_ids[0])."""
    owner = getattr(settings, "owner_chat_id", None)
    return owner is not None and user_id == owner


async def _owner_only(update: Update) -> bool:
    """True si l'utilisateur est le propriétaire du kill-switch, sinon répond et retourne False."""
    user = update.effective_user
    if user and is_owner(user.id):
        return True
    if update.message:
        await _reply(update.message, "Commande réservée au propriétaire d'ARIA (kill-switch).")
    return False


def _format_tg(text: str) -> str:
    """Plain text for Telegram — strip markdown the LLM/skills emit without parse_mode."""
    return plain_telegram(fix_handle_in_text(text))[:4000]


async def _reply(message, text: str) -> None:
    await message.reply_text(_format_tg(text))


async def _admin_check_reply(update: Update) -> bool:
    """Retourne True si l'utilisateur est admin, sinon répond avec aide diagnostic."""
    user = update.effective_user
    if not user:
        return False

    if is_admin(user.id):
        return True

    if not settings.admin_ids:
        msg = (
            "ARIA bot is running but TELEGRAM_ADMIN_IDS is not configured on the server.\n"
            f"Your Telegram user id: {user.id}\n"
            "Add it in Render Environment Variables, then redeploy."
        )
    else:
        msg = (
            "Access restricted to administrator.\n"
            f"Your id: {user.id} — not in TELEGRAM_ADMIN_IDS."
        )

    if update.message:
        await _reply(update.message, msg)
    elif update.callback_query:
        await update.callback_query.answer(msg[:200], show_alert=True)
    return False


async def apply_bot_profile_photo(image_path: Path) -> tuple[bool, str | None]:
    if not image_path.exists():
        return False, "fichier avatar introuvable"
    from io import BytesIO

    from telegram import Bot, InputProfilePhotoStatic
    from telegram.error import RetryAfter

    bot = _bot_app.bot if _bot_app else None
    owns_bot = False
    if not bot and settings.telegram_bot_token:
        bot = Bot(token=settings.telegram_bot_token)
        owns_bot = True
    if not bot:
        return False, "bot Telegram non démarré"

    bio = BytesIO(image_path.read_bytes())
    bio.seek(0)
    photo = InputProfilePhotoStatic(photo=bio)
    try:
        await bot.set_my_profile_photo(photo=photo)
        logger.info("Telegram bot profile photo updated: %s", image_path.name)
        return True, None
    except RetryAfter as exc:
        wait_min = max(1, int(exc.retry_after / 60))
        msg = (
            f"limite Telegram (flood) — réessaie dans ~{wait_min} min "
            f"ou /avatar apply"
        )
        logger.warning("Telegram profile photo flood control: %ss", exc.retry_after)
        return False, msg
    except Exception as exc:
        logger.warning("Telegram profile photo failed: %s", exc)
        return False, str(exc)
    finally:
        if owns_bot:
            await bot.shutdown()


async def send_message(text: str, chat_id: int | None = None) -> bool:
    if not _bot_app or not settings.telegram_bot_token:
        return False
    target = chat_id or settings.telegram_group_id or (settings.admin_ids[0] if settings.admin_ids else None)
    if not target:
        return False
    try:
        await _bot_app.bot.send_message(chat_id=target, text=_format_tg(text))
        return True
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


async def send_photo(path, *, caption: str = "", chat_id: int | None = None) -> bool:
    from pathlib import Path

    if not _bot_app or not settings.telegram_bot_token:
        return False
    target = chat_id or settings.telegram_group_id or (settings.admin_ids[0] if settings.admin_ids else None)
    if not target:
        return False
    file_path = Path(path)
    if not file_path.is_file():
        return False
    try:
        with file_path.open("rb") as handle:
            await _bot_app.bot.send_photo(
                chat_id=target,
                photo=handle,
                caption=_format_tg(caption) if caption else None,
            )
        return True
    except Exception as exc:
        logger.warning("Telegram photo send failed: %s", exc)
        return False


async def notify_admin(text: str) -> bool:
    """Information seule — ARIA informe, ne demande pas d'approbation."""
    return await send_message(text)


async def request_approval(action: str, description: str) -> str | None:
    if action == "contact_juno" and not settings.aria_juno_outreach:
        return None

    if settings.aria_autonomous:
        from aria_core.autonomous import execute_autonomous_action

        summary = await execute_autonomous_action(action, description)
        await notify_admin(summary)
        return "autonomous"

    if not _bot_app or not settings.admin_ids:
        return None

    req = await create_approval(action=action, description=description)
    admin_id = settings.admin_ids[0]

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"approve:{req.id}"),
            InlineKeyboardButton("❌ No", callback_data=f"reject:{req.id}"),
        ]
    ])

    text = (
        f"🔔 Approval request #{req.id}\n\n"
        f"Action: {action}\n"
        f"{description}\n\n"
        f"You decide — ARIA is waiting for your answer."
    )

    try:
        await _bot_app.bot.send_message(
            chat_id=admin_id,
            text=_format_tg(text),
            reply_markup=keyboard,
        )
        if settings.telegram_group_id and settings.telegram_group_id != admin_id:
            await _bot_app.bot.send_message(
                chat_id=settings.telegram_group_id,
                text=_format_tg(f"⏳ ARIA needs admin approval for: {action} (#{req.id})"),
            )
    except Exception as exc:
        logger.error("Approval request failed: %s", exc)
        return None

    return req.id


async def send_approval_keyboard(chat_id: int, text: str, keyboard) -> None:
    """Envoie un message avec clavier inline — utilisé par le garde-fou dépenses ACP
    (``aria_core.wallet_guard``) pour le prompt Oui/Non/Explique-moi pourquoi."""
    if not _bot_app:
        raise RuntimeError("bot Telegram non démarré")
    await _bot_app.bot.send_message(chat_id=chat_id, text=_format_tg(text), reply_markup=keyboard)


def _admin_username_label() -> str:
    user = (settings.telegram_admin_username or "").strip().lstrip("@")
    return f"@{user}" if user else "the administrator"


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Propriétaire + pause active → /start lève le kill-switch (décision opérateur).
    if user and is_owner(user.id) and outgoing_pause.is_paused():
        outgoing_pause.resume(by=user.id)
        await _reply(
            update.message,
            "▶️ ARIA reprend — actions sortantes réactivées (pause levée via /start).",
        )
        return
    if user and is_admin(user.id):
        mode = "autonomous ZHC" if settings.aria_autonomous else "approval mode"
        links = get_channel_links_text()
        await _reply(update.message, telegram_admin_start(mode, links))
        return

    admin_label = _admin_username_label()
    await _reply(
        update.message,
        telegram_visitor_start(
            settings.public_site_url,
            admin_label,
            official_telegram_bot_url(),
        ),
    )


async def _handle_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return
    from aria_core.skills.github_skill import github_configured, github_unlimited_access

    admin = is_admin(user.id)
    if admin:
        gh = "✅ configuré, accès illimité" if github_configured() and github_unlimited_access() else (
            "✅ configuré" if github_configured() else "❌ token manquant"
        )
        await _reply(
            message,
            f"ARIA — Identité opérateur\n\n"
            f"Ton ID Telegram : {user.id}\n"
            f"Rôle : ✅ OPÉRATEUR (reconnu)\n"
            f"IDs admin serveur : {settings.admin_ids}\n"
            f"Backend : {'local' if settings.debug else 'production'}\n"
            f"GitHub : {gh}\n"
            f"Peut créer/supprimer des repos : oui (GoldenFarFR/*, sauf protégés)\n\n"
            f"GitHub : texte libre (lecture seule) — ex: liste les repos ou status github",
        )
        return
    await _reply(
        message,
        f"Ton ID Telegram : {user.id}\n"
        f"Rôle : ❌ visiteur (pas admin)\n"
        f"IDs autorisés : {settings.admin_ids or 'aucun'}\n\n"
        f"Si c'est ton compte, ajoute {user.id} dans TELEGRAM_ADMIN_IDS.",
    )


async def _handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    from aria_core.llm import is_llm_configured, is_llm_provider_configured
    from aria_core.skills.github_skill import github_configured, github_unlimited_access

    user = update.effective_user
    hb = aria_heartbeat.get_status()
    last = hb.get("last_heartbeat")
    last_str = last.strftime("%H:%M UTC") if last else "never"
    llm_switch = "on" if settings.aria_llm_enabled else "off"
    provider = settings.llm_provider or "none"
    provider_ok = is_llm_provider_configured()
    chat_llm = "active" if is_llm_configured() else "off"
    gh = "unlimited ✅" if github_configured() and github_unlimited_access() else (
        "configured" if github_configured() else "missing"
    )
    from aria_core.gateway.x_twitter import is_x_post_configured, is_x_read_configured
    from aria_core.identity import official_x_at as x_at

    x_post = "connected ✅" if is_x_post_configured() else "missing keys"
    x_read = "bearer ✅" if is_x_read_configured() else "off"
    from aria_core.capability_levels import global_index, check_auto_completions

    check_auto_completions()
    qi = global_index()
    pst = outgoing_pause.pause_status()
    if not pst["readable"]:
        sorties = "⚠️ état illisible — dépenses gelées (fail-closed), tweets/jobs actifs"
    elif pst["paused"]:
        sorties = f"⏸ EN PAUSE {outgoing_pause.since_label()}"
    else:
        sorties = "actives ▶️"
    await _reply(
        update.message,
        f"ARIA — Status (opérateur)\n"
        f"Your ID: {user.id if user else '?'} — admin ✅\n"
        f"Sorties (tweets/X/dépenses/jobs): {sorties}\n"
        f"Indice ARIA: {qi} / 1000 (demande en texte libre)\n"
        f"Heartbeat: {last_str}\n"
        f"Telegram: {get_mode()} ✅\n"
        f"X {x_at()}: post {x_post} · read {x_read}\n"
        f"LLM chat: {chat_llm}\n"
        f"ARIA_LLM_ENABLED: {llm_switch}\n"
        f"Provider ({provider}): {'configured' if provider_ok else 'missing'}\n"
        f"GitHub: {gh}\n"
        f"Public grounded: {'on' if settings.aria_grounded_mode else 'off'}\n"
        f"Telegram chat: founder LLM (opinion OK)\n"
        f"Proactive ideas: {'on' if settings.aria_proactive_ideas else 'off'}\n"
        f"Access gate: {'on' if settings.access_code_enabled else 'off'}",
    )


async def _reply_handles_registry(message, args: list[str]) -> None:
    """Liste ou modifie le registre handles X (args = action + paramètres)."""
    from aria_core.handle_registry import (
        add_handle,
        format_registry_help,
        registry_status,
        remove_handle,
        set_alias,
        set_default_pack,
    )

    action = (args[0].lower() if args else "list").strip()
    try:
        if action in ("list", "help", "aide", ""):
            await _reply(message, format_registry_help())
            return
        if action == "add" and len(args) >= 2:
            role = " ".join(args[2:]).strip() if len(args) > 2 else "custom"
            await _reply(message, add_handle(args[1], role=role))
            return
        if action == "remove" and len(args) >= 2:
            await _reply(message, remove_handle(args[1]))
            return
        if action == "alias" and len(args) >= 3:
            await _reply(message, set_alias(args[1], args[2:]))
            return
        if action == "pack" and len(args) >= 2:
            await _reply(message, set_default_pack(args[1]))
            return
        if action == "json":
            import json

            await _reply(message, json.dumps(registry_status(), ensure_ascii=False, indent=2)[:3800])
            return
    except ValueError as exc:
        await _reply(message, str(exc))
        return
    await _reply(
        message,
        "Usage : /handles | add <handle> | remove <handle> | "
        "alias <nom> h1 h2 | pack veille",
    )


async def _handle_handles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    await _reply_handles_registry(message, args)


async def _handle_x(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.gateway.x_twitter import (
        is_x_post_configured,
        is_x_read_configured,
        post_tweet,
        verify_x_connection,
        x_status,
    )
    from aria_core.identity import official_x_at

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "status").strip()

    if sub in ("status", "help", "aide", ""):
        from aria_core.x_publication_policy import ledger_summary, policy_summary

        from aria_core.gateway.x_engagement import mentions_learn_enabled, mentions_reply_enabled

        st = x_status()
        ok, verify_msg = await verify_x_connection() if st["post"] else (False, "OAuth keys manquantes")
        led = ledger_summary()
        status_block = (
            f"X — {official_x_at()}\n\n"
            f"Lecture (Bearer): {'oui' if is_x_read_configured() else 'non'}\n"
            f"Publication (OAuth): {'oui' if is_x_post_configured() else 'non'}\n"
            f"Vérification: {verify_msg}\n"
            f"Mentions → mémoire: {'oui' if mentions_learn_enabled() else 'non (X_MENTIONS_LEARN_ENABLED)'}\n"
            f"Veille X (curiosity): {'oui' if getattr(settings, 'x_curiosity_enabled', False) else 'non (X_CURIOSITY_ENABLED)'}\n"
            f"Likes sur réponses: {'oui' if settings.x_allow_likes else 'non (X_ALLOW_LIKES)'}\n"
            f"Reply auto sur X: {'oui (~90 min)' if mentions_reply_enabled() else 'non (X_ALLOW_REPLIES)'}\n"
            f"Tweets aujourd'hui: {led['posts_today']}\n"
            f"Dépense estimée: {led['estimated_spend_usd']:.3f} $ "
            f"/ {led.get('spend_cap_usd', led.get('aria_spend_cap_usd', 1)):.2f} $ cap\n\n"
            f"{policy_summary('fr')}\n\n"
        )
        try:
            from aria_core.handle_registry import format_registry_short

            status_block += f"{format_registry_short()}\n\n"
        except Exception:
            pass
        status_block += (
            "Commandes:\n"
            "/handles — alias @holding @veille\n"
            "X : texte libre (ex: compose un tweet sur ... )"
        )
        await _reply(message, status_block)
        return

    if sub == "handles":
        await _reply_handles_registry(message, args[1:])
        return

    if sub == "compose":
        from aria_core.tweet_compose_workflow import (
            reset_workflow,
            start_compose_workflow,
            workflow_status,
        )

        action = (args[1].lower() if len(args) > 1 else "start").strip()
        if action == "cancel":
            await _reply(message, reset_workflow())
            return
        if action == "status":
            await _reply(message, workflow_status())
            return
        out = await start_compose_workflow()
        await _reply(message, out)
        return

    if sub == "policy":
        from aria_core.x_publication_policy import ensure_policy_file, ledger_summary, policy_summary

        ensure_policy_file()
        led = ledger_summary()
        await _reply(
            message,
            f"{policy_summary('fr')}\n\n"
            f"Ledger: {led['posts_today']} post(s) today, "
            f"{led['estimated_spend_usd']:.3f} $ / {led.get('spend_cap_usd', 1):.2f} $ cap ce mois.",
        )
        return

    if sub == "post":
        body = " ".join(args[1:]).strip()
        if not body:
            await _reply(message, "Usage: /x post <texte du tweet>")
            return
        _, note = await post_tweet(body, approval_id="telegram")
        await _reply(message, note)
        return

    if sub == "profile":
        from aria_core.x_profile import (
            canonical_x_profile,
            fetch_live_x_profile,
            format_profile_summary,
            profile_fields_differ,
            sync_x_profile,
        )

        action = (args[1].lower() if len(args) > 1 else "status").strip()
        if action in ("preview", "cible", "target"):
            await _reply(message, f"Profil cible @Aria_ZHC\n\n{format_profile_summary(lang='fr')}")
            return
        if action in ("sync", "apply", "force"):
            force = action == "force" or "force" in text.lower()
            result = await sync_x_profile(force=force)
            if result.get("synced"):
                drift = ", ".join(result.get("drift") or []) or "complet"
                await _reply(message, f"Profil X synchronisé — champs : {drift}")
                return
            if result.get("skipped"):
                reason = result.get("reason", "?")
                await _reply(message, f"Profil X — rien à faire ({reason}).\n\n{format_profile_summary()}")
                return
            err = result.get("error") or result.get("reason") or "échec"
            await _reply(message, f"Sync profil X : {err}")
            return

        target = canonical_x_profile()
        try:
            live = await fetch_live_x_profile()
            drift = profile_fields_differ(live, target)
            lines = [
                "Profil X — état",
                "",
                "Cible :",
                format_profile_summary(lang="fr"),
                "",
                "Live :",
                f"Nom : {live.get('name', '—')}",
                f"Bio : {live.get('description', '—')}",
                f"Site : {live.get('url', '—')}",
                f"Lieu : {live.get('location', '—')}",
            ]
            if drift:
                lines.append(f"\nDérive : {', '.join(drift)}")
                lines.append("→ /x profile sync")
            else:
                lines.append("\nAligné sur la narrative Vanguard.")
            await _reply(message, "\n".join(lines))
        except Exception as exc:
            await _reply(
                message,
                f"Profil cible :\n{format_profile_summary()}\n\nLive indisponible : {exc}",
            )
        return

    await _reply(message, "Usage: /x status | /x profile | /x handles | /x compose | /x post <texte>")


async def _handle_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.locale import detect_lang
    from aria_core.skills.github_skill import execute_github_sandbox

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "status").strip()
    lang = detect_lang(text)

    if sub in ("status", "config", "aide", "help", ""):
        out, _ = await execute_github_sandbox("github status", lang)
        await _reply(message, out)
        return

    if sub in ("list", "liste", "repos"):
        out, _ = await execute_github_sandbox("liste tous les repos", lang)
        await _reply(message, out)
        return

    if sub in ("create", "creer", "créer", "new", "nouveau"):
        name = " ".join(args[1:]).strip()
        if not name:
            await _reply(message, "Usage: /github create <nom-du-repo>")
            return
        out, _ = await execute_github_sandbox(f"créer repo {name}", lang)
        await _reply(message, out)
        return

    if sub in ("delete", "supprimer", "remove", "del"):
        name = " ".join(args[1:]).strip()
        if not name:
            await _reply(message, "Usage: /github delete <nom-du-repo>\nEx: /github delete kikou")
            return
        out, _ = await execute_github_sandbox(f"supprime repo {name}", lang)
        await _reply(message, out)
        return

    await _reply(
        message,
        "Usage: /github status | list | create <nom> | delete <nom>\n"
        "Ou en texte libre : supprime repo kikou",
    )


async def _handle_repertoire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.locale import detect_lang
    from aria_core.skills.repertoire_skill import execute_manage_repertoire

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "list").strip()
    lang = detect_lang(text)

    if sub in ("list", "liste", "help", "aide", ""):
        out, _ = await execute_manage_repertoire("répertoire list", lang)
        await _reply(message, out)
        return

    if sub in ("delete", "supprimer", "remove", "del"):
        name = " ".join(args[1:]).strip()
        if not name:
            await _reply(message, "Usage: /repertoire delete <nom du projet>")
            return
        out, _ = await execute_manage_repertoire(f"supprime du répertoire {name}", lang)
        await _reply(message, out)
        return

    if sub in ("archive", "archiver"):
        name = " ".join(args[1:]).strip()
        if not name:
            await _reply(message, "Usage: /repertoire archive <nom du projet>")
            return
        out, _ = await execute_manage_repertoire(f"archive du répertoire {name}", lang)
        await _reply(message, out)
        return

    await _reply(message, "Usage: /repertoire list | delete <nom> | archive <nom>")


async def _handle_qi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.skills.capability_skill import execute_capability

    out, _ = await execute_capability("montre qi aria", lang="fr")
    await _reply(message, out)


async def _handle_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    if text in ("/level", "/level@Aria_ZHC_Bot") or text.split()[0] == "/level" and len(text.split()) == 1:
        from aria_core.skills.capability_skill import execute_capability

        out, _ = await execute_capability("niveaux aria", lang="fr")
        await _reply(
            message,
            out + "\n\nUsage: /level up codage [note optionnelle]",
        )
        return
    from aria_core.skills.capability_skill import execute_capability

    out, _ = await execute_capability(text, lang="fr")
    await _reply(message, out)


async def _handle_directive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()
    if not body:
        await _reply(message, "Usage: /directive <permanent rule for ARIA>")
        return
    from aria_core.directives import append_directive

    append_directive(body)
    await _reply(message, "Directive saved — ARIA will prioritize this in every LLM call.")


async def _handle_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()
    if "|" not in body:
        await _reply(
            message,
            "Usage: /learn <topic> | <lesson>\n"
            "Example: /learn optimization | Always flush DNS cache before blaming Render",
        )
        return
    topic, content = [p.strip() for p in body.split("|", 1)]
    if not topic or not content:
        await _reply(message, "Topic and lesson required.")
        return
    from aria_core.knowledge.memory_triage import triaged_add_knowledge
    from aria_core.memory import append_memory

    item, triage_result = await triaged_add_knowledge(
        source="manual",
        topic=topic[:64],
        content=content[:2000],
        confidence=0.95,
        approved=True,
        skip_triage=False,
    )
    if item is None:
        await _reply(message, f"Rejeté par triage mémoire : {triage_result}")
        return
    append_memory("curiosity", f"[manual] [{topic}] {content[:120]}")
    await _reply(message, f"Learned ✅ [{item.id}] {topic}: {content[:200]}...")


async def _handle_calibrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()
    if not body:
        await _reply(
            message,
            "Usage: /calibrate <affirmation> | vrai|faux|incertain | [source]\n"
            "Ex: /calibrate DEXPulse est une filiale | vrai | holding",
        )
        return
    from aria_core.skills.calibrate_skill import execute_calibrate

    out, _data = await execute_calibrate(body, lang="fr")
    await _reply(message, out)


async def _handle_avatar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.avatar import (
        apply_avatar_sync,
        aria_choose_avatar,
        get_avatar_status,
        list_gallery,
        pick_gallery_avatar,
    )

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "status").strip()

    if sub in ("status", "help", "aide", ""):
        from aria_core.avatar_identity import format_identity_status

        status = get_avatar_status()
        gallery = ", ".join(g["id"] for g in status["gallery"])
        current = status.get("current") or {}
        await _reply(
            message,
            "ARIA — photo de profil\n\n"
            f"Actuelle : {current.get('source', 'aucune')}\n"
            f"Note : {current.get('note', '—')}\n"
            f"URL publique : {settings.public_site_url.rstrip('/')}/api/aria/avatar\n\n"
            f"{format_identity_status()}\n\n"
            f"Galerie : {gallery}\n\n"
            "Commandes :\n"
            "/avatar identity — prochaine photo = visage de référence (identité verrouillée)\n"
            "/avatar scene <lieu> — même personnage, nouveau décor\n"
            "/avatar style — cycle Grok Imagine (14 jours minimum)\n"
            "/avatar apply — resynchroniser Telegram + X\n"
            "Photo : légende /avatar (même visage que l'ancre ensuite)",
        )
        return

    if sub == "identity":
        from aria_core.avatar_identity import (
            format_identity_status,
            has_identity_anchor,
            reset_identity_anchor,
            set_pending_identity_anchor,
        )

        action = (args[1].lower() if len(args) > 1 else "").strip()
        if action == "reset":
            reset_identity_anchor()
            await _reply(
                message,
                "Identité réinitialisée.\n"
                "Envoie la nouvelle photo de référence (légende /avatar).",
            )
            return
        if not has_identity_anchor():
            set_pending_identity_anchor(True)
            await _reply(
                message,
                "Identité visuelle — en attente de TA photo de référence.\n\n"
                "Envoie une photo avec /avatar (ou « photo de profil »).\n"
                "Ce visage sera conservé sur toutes les futures photos "
                "(parc, monument, vacances…).\n\n"
                "Puis : /avatar scene Tour Eiffel — ou envoie une autre photo du même visage.",
            )
            return
        await _reply(message, format_identity_status())
        return

    if sub == "scene" and len(args) >= 2:
        from aria_core.avatar import format_avatar_sync_status
        from aria_core.avatar_identity import apply_scene_portrait

        scene = " ".join(args[1:]).strip()
        try:
            entry = await apply_scene_portrait(scene)
            sync = entry.get("sync") or {}
            await _reply(
                message,
                f"Nouveau portrait — {scene}\n{format_avatar_sync_status(sync)}",
            )
        except Exception as exc:
            await _reply(message, f"Scène : {exc}")
        return

    if sub == "choose":
        from aria_core.avatar_identity import is_identity_locked

        if is_identity_locked():
            await _reply(
                message,
                "Identité visuelle verrouillée — galerie désactivée.\n"
                "Envoie une photo du même personnage ou /avatar scene <lieu>.",
            )
            return
        from aria_core.avatar import format_avatar_sync_status

        pick_id = await aria_choose_avatar()
        status = get_avatar_status()
        note = (status.get("current") or {}).get("note", "")
        detail = f"\n{note}" if note else ""
        sync = (status.get("current") or {}).get("sync") or {}
        await _reply(
            message,
            f"Nouvelle photo : {pick_id}\n{format_avatar_sync_status(sync)}{detail}",
        )
        return

    if sub == "pick" and len(args) >= 2:
        from aria_core.avatar import format_avatar_sync_status
        from aria_core.avatar_identity import is_identity_locked

        if is_identity_locked():
            await _reply(
                message,
                "Identité visuelle verrouillée — utilise une photo réelle du même personnage.",
            )
            return
        try:
            entry = await pick_gallery_avatar(args[1], note=f"Operator pick {args[1]}")
            sync = entry.get("sync") or {}
            await _reply(
                message,
                f"Photo {args[1]} active — {format_avatar_sync_status(sync)}",
            )
        except FileNotFoundError:
            ids = ", ".join(g["id"] for g in list_gallery())
            await _reply(message, f"Inconnu. IDs disponibles : {ids}")
        return

    if sub == "style":
        from aria_core.avatar import format_avatar_sync_status
        from aria_core.avatar_style_refresh import (
            apply_pending_style,
            discard_pending,
            format_refresh_status,
            run_refresh_cycle,
            update_config,
        )
        from aria_core.gateway.telegram_bot import send_photo

        action = (args[1].lower() if len(args) > 1 else "status").strip()
        if action in ("status", "help", ""):
            await _reply(message, format_refresh_status())
            return
        if action == "now":
            try:
                result = await run_refresh_cycle(notify=False)
                if result.get("skipped"):
                    await _reply(message, f"Style : ignoré — {result.get('reason', '?')}")
                    return
                pending = result.get("pending") or {}
                from aria_core.avatar_style_refresh import pending_preview_path

                path = pending_preview_path()
                await _reply(
                    message,
                    "🎨 Aperçu généré (non appliqué).\n"
                    f"{pending.get('style_prompt', '')[:400]}\n\n"
                    "/avatar style apply — valider · /avatar style skip — refuser",
                )
                if path:
                    await send_photo(path, caption="Aperçu style ARIA", chat_id=message.chat_id)
            except Exception as exc:
                await _reply(message, f"Style : {exc}")
            return
        if action == "apply":
            try:
                result = await apply_pending_style(note="Validé opérateur Telegram")
                sync = (result.get("current") or {}).get("sync") or {}
                await _reply(
                    message,
                    f"Style appliqué ✅\n{format_avatar_sync_status(sync)}",
                )
            except Exception as exc:
                await _reply(message, f"Apply : {exc}")
            return
        if action in ("skip", "discard", "refuse"):
            await _reply(message, discard_pending())
            return
        if action == "interval" and len(args) >= 3:
            try:
                days = int(args[2])
                st = update_config(interval_days=days)
                await _reply(message, f"Intervalle : {st['interval_days']} jours.")
            except (ValueError, TypeError) as exc:
                await _reply(message, f"Intervalle : {exc} (14 jours)")
            return
        if action in ("on", "off"):
            st = update_config(enabled=(action == "on"))
            await _reply(message, f"Style périodique : {'activé' if st['enabled'] else 'désactivé'}.")
            return
        if action == "propose":
            try:
                from aria_core.avatar_style_refresh import propose_style

                style = await propose_style(force_new=True)
                await _reply(message, f"Style proposé :\n\n{style}")
            except Exception as exc:
                await _reply(message, f"Propose : {exc}")
            return
        await _reply(
            message,
            "Style avatar (Grok Imagine) :\n"
            "/avatar style — statut\n"
            "/avatar style now — générer aperçu\n"
            "/avatar style apply — appliquer l'aperçu\n"
            "/avatar style skip — refuser\n"
            "/avatar style interval 14 — fréquence (minimum 14 jours)\n"
            "/avatar style on|off",
        )
        return

    if sub == "apply":
        from aria_core.avatar import format_avatar_sync_status

        sync = await apply_avatar_sync()
        await _reply(message, f"Sync photo : {format_avatar_sync_status(sync)}")
        return

    await _reply(
        message,
        "Usage : /avatar | /avatar identity | /avatar scene <lieu> | /avatar style | /avatar apply",
    )


def _caption_is_avatar_upload(caption: str) -> bool:
    text = (caption or "").strip()
    if not text:
        return True
    lower = text.lower()
    if lower.startswith("/avatar"):
        return True
    return bool(
        re.search(r"avatar|photo de profil|profile photo|profil|mets.*photo|met.*photo", lower)
    )


async def _handle_avatar_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message or not message.photo:
        return
    caption = (message.caption or "").strip()
    if not _caption_is_avatar_upload(caption):
        return
    from aria_core.avatar import format_avatar_sync_status
    from aria_core.avatar_identity import (
        caption_requests_identity,
        has_identity_anchor,
        is_pending_identity_anchor,
        set_profile_with_identity,
    )

    photo = message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    data = bytes(await tg_file.download_as_bytearray())
    note = caption
    if note.lower().startswith("/avatar"):
        note = caption.replace("/avatar", "", 1).strip()
    note = note or "Custom upload"
    force = (
        caption_requests_identity(caption)
        or is_pending_identity_anchor()
        or not has_identity_anchor()
    )
    try:
        entry = await set_profile_with_identity(
            data,
            source="telegram_upload",
            note=note,
            force_establish=force,
        )
    except ValueError as exc:
        await _reply(message, str(exc))
        return
    sync = entry.get("sync") or {}
    id_note = ""
    if entry.get("identity", {}).get("established"):
        id_note = "\nIdentité visuelle établie — ce visage sera conservé."
    elif entry.get("identity", {}).get("verified"):
        id_note = "\nMême personnage vérifié ✅"
    await _reply(
        message,
        f"Photo de profil enregistrée.{id_note}\n{format_avatar_sync_status(sync)}",
    )


async def _handle_experiment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()
    if not body:
        await _reply(
            message,
            "Usage: /experiment <name> [description]\n"
            "Example: /experiment ui-v2 Dark mode holding page prototype",
        )
        return
    from aria_core.locale import detect_lang
    from aria_core.skills.github_skill import execute_github_sandbox

    lang = detect_lang(body)
    prompt = f"create experiment sandbox {body}"
    out, _ = await execute_github_sandbox(prompt, lang)
    await _reply(message, out)


async def _handle_public_message(update: Update, text: str) -> None:
    """Courtesy + verified info only — no operator tools."""
    message = update.message
    user = update.effective_user
    if not message or not user:
        return

    allowed = check_rate_limit(
        f"telegram_visitor:{user.id}",
        max_attempts=settings.aria_chat_rate_limit_per_hour,
        window_seconds=3600,
    )
    if not allowed:
        await _reply(
            message,
            "Limite atteinte — réessaie dans une heure."
            if settings.aria_telegram_lang == "fr"
            else "Rate limit reached — try again in an hour.",
        )
        return

    from aria_core.locale import detect_lang

    await message.reply_chat_action("typing")
    lang = detect_lang(text)
    response = await aria_brain.process(
        text,
        lang=lang,
        public_mode=True,
        visitor_id=f"tg-{user.id}",
    )
    await _reply(message, response.reply)


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    lower = text.lower()
    user = update.effective_user

    if not user or not is_admin(user.id):
        await _handle_public_message(update, text)
        return

    from aria_core.tweet_compose_workflow import handle_workflow_message

    wf_reply = await handle_workflow_message(text)
    if wf_reply is not None:
        await _reply(message, wf_reply)
        return

    from aria_core.curiosity import (
        approve_pending_knowledge,
        parse_learn_approval,
        reject_pending_knowledge,
    )

    from aria_core.knowledge.app_idea_poll import parse_app_vote, record_app_vote

    app_vote = parse_app_vote(text)
    if app_vote is not None:
        reply = record_app_vote(app_vote, lang="fr")
        await _reply(message, reply)
        return

    learn_reply = parse_learn_approval(text)
    if learn_reply is not None:
        if learn_reply:
            count = await approve_pending_knowledge()
            if count > 0:
                reply = f"Approuvé — {count} insight(s) ajouté(s) en mémoire cognitive."
            elif settings.aria_autonomous:
                reply = (
                    "Déjà intégré — le mode autonome ZHC a traité ces insights "
                    "sans attendre ta réponse."
                )
            else:
                reply = "Rien en attente — aucun insight à approuver pour le moment."
            await _reply(message, reply)
        else:
            count = await reject_pending_knowledge()
            if count > 0:
                reply = f"Refusé — {count} insight(s) ignoré(s)."
            else:
                reply = "Rien en attente à refuser."
            await _reply(message, reply)
        return

    if lower.startswith("published "):
        exchange_id = text.split(maxsplit=1)[1].strip()
        ex = await mark_published(exchange_id)
        if ex:
            await _reply(message, f"Exchange #{exchange_id} marked as published.")
        else:
            await _reply(message, f"Exchange #{exchange_id} not found.")
        return

    if lower.startswith("reply "):
        parts = text.split(maxsplit=2)
        if len(parts) >= 3:
            exchange_id, reply_text = parts[1], parts[2]
            ex = await record_reply(exchange_id, reply_text)
            if ex:
                await _reply(message, f"Reply logged for exchange #{exchange_id}. ✅")
            else:
                await _reply(message, f"Exchange #{exchange_id} not found.")
            return

    from aria_core.locale import detect_lang

    lang = detect_lang(text)

    from aria_core.self_maintenance import handle_operator_self_message

    sm_reply = await handle_operator_self_message(text, lang=lang)
    if sm_reply is not None:
        await message.reply_chat_action("typing")
        await _reply(message, sm_reply)
        return

    if re.search(r"avatar|photo de profil|profile photo|profil", lower) and re.search(
        r"choisi|choose|change|nouvelle|new|mets|met ", lower
    ):
        from aria_core.avatar import aria_choose_avatar, format_avatar_sync_status, get_avatar_status

        try:
            pick_id = await aria_choose_avatar()
            status = get_avatar_status()
            sync = (status.get("current") or {}).get("sync") or {}
            await _reply(
                message,
                f"Nouvelle photo de profil : {pick_id}\n{format_avatar_sync_status(sync)}",
            )
        except Exception as exc:
            await _reply(message, f"Avatar : échec ({exc})")
        return

    from aria_core.skills.github_skill import (
        execute_github_sandbox,
        looks_like_repo_create,
        looks_like_repo_delete,
    )

    if looks_like_repo_delete(text) or looks_like_repo_create(text):
        await message.reply_chat_action("typing")
        try:
            out, _ = await execute_github_sandbox(text, lang)
            await _reply(message, out)
        except Exception as exc:
            logger.exception("GitHub command failed")
            await _reply(
                message,
                f"Échec GitHub : {exc}\n\nRéessaie : /github delete <nom>",
            )
        return

    await message.reply_chat_action("typing")
    try:
        response = await aria_brain.process(text, lang=lang, public_mode=False)
        await _reply(message, response.reply)
    except Exception as exc:
        logger.exception("Telegram brain.process failed")
        await _reply(
            message,
            f"Erreur interne : {exc.__class__.__name__}\n"
            f"Pour supprimer un repo : /github delete kikou",
        )


async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await _admin_check_reply(update):
        return

    data = query.data or ""
    if ":" not in data:
        await query.answer()
        return

    action, approval_id = data.split(":", 1)

    if action == "explain":
        await query.answer()
        req = await get_approval(approval_id)
        if not req or req.status != ApprovalStatus.PENDING or not req.action.startswith("spend:"):
            await send_message("Demande introuvable ou déjà traitée.", query.from_user.id)
            return

        import json as _json

        from aria_core.wallet_guard import generate_spend_explanation, send_spend_prompt

        spend_action = req.action.split(":", 1)[1]
        payload = _json.loads(req.payload or "{}")
        explanation = await generate_spend_explanation(spend_action, req.description, payload)
        await send_message(f"🤔 {explanation}", query.from_user.id)
        await send_spend_prompt(approval_id, spend_action, req.description)
        return

    approved = action == "approve"

    # Kill-switch : un « Oui » sur une dépense pendant la pause ne doit rien exécuter NI
    # consommer l'approbation — sinon, l'approbation résolue + ledger pending laisserait la
    # dépense coincée après /start. On laisse tout en attente : re-cliquable une fois repris.
    # (Un « Non » reste autorisé : aucun argent ne sort.)
    if approved:
        req = await get_approval(approval_id)
        if req and req.action.startswith("spend:"):
            _spend_block = outgoing_pause.money_block_reason(
                f"L'exécution de la dépense #{approval_id}"
            )
            if _spend_block:
                await query.answer("⛔ Dépense gelée", show_alert=True)
                await send_message(
                    _spend_block
                    + "\nLa demande reste en attente : re-clique « Oui » après reprise.",
                    query.from_user.id,
                )
                return

    result = await resolve_approval(approval_id, approved, str(query.from_user.id))

    if not result:
        await query.answer("Request not found or already handled.", show_alert=True)
        return

    label = "approved ✅" if approved else "rejected ❌"

    if result.action.startswith("spend:"):
        from aria_core.wallet_guard import resolve_spend

        try:
            outcome = await resolve_spend(approval_id, approved, str(query.from_user.id))
        except Exception:
            logger.exception("resolve_spend a levé pour le spend #%s", approval_id)
            await query.edit_message_text(
                _format_tg(
                    f"Request #{approval_id}\nAction: {result.action}\n"
                    "⚠️ exception lors de l'exécution — vérifie le ledger"
                ),
            )
            await send_message(
                f"⚠️ Spend #{approval_id} : exception lors de l'exécution — vérifie le ledger",
                query.from_user.id,
            )
            return
        # Édition APRÈS resolve_spend : "approved ✅" ne s'affiche que si le spend a résolu sans exception
        await query.edit_message_text(
            _format_tg(f"Request #{approval_id} {label}\nAction: {result.action}"),
        )
        await send_message(outcome, query.from_user.id)
        return

    await query.edit_message_text(
        _format_tg(f"Request #{approval_id} {label}\nAction: {result.action}"),
    )

    if approved and result.action == "contact_juno":
        _, instructions = await execute_contact_juno(approval_id)
        await send_message(instructions, query.from_user.id)

    if approved and result.action == "learn_knowledge":
        from aria_core.curiosity import approve_pending_knowledge
        count = await approve_pending_knowledge()
        await send_message(
            f"Knowledge approved — {count} insights added to cognitive memory.",
            query.from_user.id,
        )

    if not approved and result.action == "learn_knowledge":
        from aria_core.curiosity import reject_pending_knowledge
        count = await reject_pending_knowledge()
        await send_message(f"Knowledge rejected — {count} pending insights discarded.", query.from_user.id)

    if approved and settings.telegram_group_id and result.action not in ("contact_juno", "learn_knowledge"):
        await send_message(
            f"✅ Admin approved: {result.action}\n{result.description}",
            settings.telegram_group_id,
        )


async def _register_bot_commands() -> None:
    """Enregistre le menu / visible dans Telegram (bouton Menu du bot)."""
    if not _bot_app:
        return
    from telegram import BotCommand

    commands = [
        BotCommand("start", f"Welcome — {holding_name()}"),
        BotCommand("status", "Heartbeat, LLM state, GitHub read"),
        BotCommand("stop", "⏸ Pause immédiate des actions sortantes"),
        BotCommand("resume", "▶️ Reprendre les actions sortantes"),
        BotCommand("scan", "🔎 Scan on-chain lecture seule (adresse contrat)"),
    ]
    await _bot_app.bot.set_my_commands(commands)
    logger.info("Telegram command menu registered (%d commands)", len(commands))


async def _handle_test_spend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/test_spend — escalade wallet de test (admin) : exécuteur mocké, aucune dépense réelle."""
    message = update.message
    if not message:
        return
    if not await _admin_check_reply(update):
        return

    from aria_core import wallet_guard
    from aria_core.wallet_guard import SpendEscalationError

    def _mock_test_executor(payload: dict) -> tuple[dict | None, str | None]:
        # Jamais acp_cli — action dédiée "test_spend", absente des chemins de dépense réels.
        return ({"status": "test_ok", "mock": True, "amount_usdc": payload.get("amount_usdc")}, None)

    wallet_guard.WALLET_ACTIONS.setdefault("test_spend", _mock_test_executor)

    try:
        approval_id = await wallet_guard.escalate_spend(
            "test_spend",
            amount="0.10 USDC",
            counterparty="TEST-JOB-000001",
            description="Escalade de TEST — exécuteur mocké, aucune dépense réelle.",
            payload={"job_id": "TEST-JOB-000001", "amount_usdc": 0.10, "test": True},
        )
    except SpendEscalationError as exc:
        await _reply(message, str(exc))
        return

    await _reply(
        message,
        f"⏳ Escalade de test #{approval_id} envoyée — réponds au prompt "
        "Oui / Non / Explique-moi pourquoi pour valider le flux.",
    )


_SCAN_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


async def _handle_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scan <adresse> — lecture seule : score de risque on-chain (DexScreener + Blockscout)."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    address = body.strip()
    if not _SCAN_ADDR_RE.match(address):
        await _reply(
            message,
            "Usage : /scan <adresse_contrat>\n"
            "Adresse invalide — attendu : 0x suivi de 40 caractères hexadécimaux.",
        )
        return

    from aria_core.skills.acp_onchain_scan import scan_base_token

    ctx = await scan_base_token(address)

    lines = [
        f"🔎 Scan {address}",
        f"Score sécurité : {ctx.security_score}/100 — {ctx.lite_verdict}",
        f"Source : {ctx.data_source} ({ctx.pairs_found} paire(s) trouvée(s))",
    ]
    if ctx.risk_flags:
        lines.append("")
        lines.append("Flags :")
        lines.extend(f"- {flag}" for flag in ctx.risk_flags)
    else:
        lines.append("Aucun flag de risque détecté.")

    await _reply(message, "\n".join(lines))


async def _handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop — kill-switch : pause immédiate de toutes les actions sortantes (propriétaire uniquement)."""
    if not await _owner_only(update):
        return
    user = update.effective_user
    outgoing_pause.pause(by=user.id if user else None)
    await _reply(
        update.message,
        "⏸ ARIA en pause — tweets, réponses/likes X, dépenses ACP et jobs planifiés suspendus.\n"
        "Tes commandes manuelles sont aussi bloquées le temps de la pause.\n"
        "Envoie /start (ou /resume) pour reprendre.",
    )


async def _handle_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resume — lève le kill-switch (propriétaire uniquement). Alias explicite de /start en pause."""
    if not await _owner_only(update):
        return
    if not outgoing_pause.is_paused():
        await _reply(update.message, "▶️ ARIA n'était pas en pause — rien à reprendre.")
        return
    user = update.effective_user
    outgoing_pause.resume(by=user.id if user else None)
    await _reply(update.message, "▶️ ARIA reprend — actions sortantes réactivées.")


def _register_handlers(app: Application) -> None:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    # Minimal commands only (user request)
    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(CommandHandler("status", _handle_status))
    app.add_handler(CommandHandler("stop", _handle_stop))
    app.add_handler(CommandHandler("resume", _handle_resume))
    app.add_handler(CommandHandler("test_spend", _handle_test_spend))
    app.add_handler(CommandHandler("scan", _handle_scan))

    # Inline keyboard buttons (approve/reject/explain — approvals + wallet spend flow)
    app.add_handler(CallbackQueryHandler(_handle_callback))

    # All other interactions via plain text (no slash commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))


async def process_webhook_update(payload: dict) -> None:
    if not _bot_app:
        raise RuntimeError("Bot not initialized")
    from telegram import Update

    update = Update.de_json(payload, _bot_app.bot)
    await _bot_app.process_update(update)


async def _reset_telegram_state() -> None:
    global _bot_app, _webhook_mode
    _bot_app = None
    _webhook_mode = False


async def start_telegram_bot() -> None:
    global _bot_app, _webhook_mode

    if not settings.telegram_bot_token:
        logger.info("Telegram disabled — TELEGRAM_BOT_TOKEN not set")
        return

    from telegram.ext import Application

    bot_app = Application.builder().token(settings.telegram_bot_token).build()
    _register_handlers(bot_app)
    _bot_app = bot_app

    try:
        await bot_app.initialize()
        await bot_app.start()
        await get_bot_username()
        await _register_bot_commands()

        webhook_url = settings.telegram_webhook_url
        if settings.use_telegram_webhook and webhook_url:
            webhook_kwargs: dict = {"url": webhook_url, "drop_pending_updates": False}
            if settings.telegram_webhook_secret:
                webhook_kwargs["secret_token"] = settings.telegram_webhook_secret
            else:
                logger.warning("TELEGRAM_WEBHOOK_SECRET not set — webhook without secret token")
            await bot_app.bot.set_webhook(**webhook_kwargs)
            _webhook_mode = True
            logger.info("Telegram webhook active: %s", webhook_url)

            if settings.admin_ids:
                try:
                    await send_message(
                        telegram_online_notice("webhook mode"),
                        settings.admin_ids[0],
                    )
                except Exception as exc:
                    logger.warning("Telegram online notice failed (webhook kept): %s", exc)
            try:
                from aria_core.avatar import ensure_avatar_ready

                await ensure_avatar_ready()
            except Exception as exc:
                logger.warning("Avatar bootstrap: %s", exc)
        else:
            try:
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                await bot_app.updater.start_polling(drop_pending_updates=True)
                _webhook_mode = False
                logger.info("Telegram polling active (local dev)")

                if settings.admin_ids:
                    await send_message(
                        telegram_online_notice("polling mode")
                        + "\nStop local backend if Render is also running — one instance only.",
                        settings.admin_ids[0],
                    )
                try:
                    from aria_core.avatar import ensure_avatar_ready

                    await ensure_avatar_ready()
                except Exception as exc:
                    logger.warning("Avatar bootstrap: %s", exc)
            except Exception as exc:
                logger.error(
                    "Telegram polling failed (another instance may be running): %s", exc
                )
                if settings.admin_ids:
                    await send_message(
                        "⚠️ ARIA polling conflict — stop duplicate backends or use Render webhook.",
                        settings.admin_ids[0],
                    )
    except Exception:
        await stop_telegram_bot()
        raise


async def stop_telegram_bot() -> None:
    global _bot_app, _webhook_mode
    if not _bot_app:
        return

    app = _bot_app
    webhook = _webhook_mode
    await _reset_telegram_state()

    try:
        if not webhook:
            try:
                if getattr(app.updater, "running", False):
                    await app.updater.stop()
            except RuntimeError:
                pass
    except Exception as exc:
        logger.warning("Telegram updater stop: %s", exc)

    try:
        if getattr(app, "running", False):
            await app.stop()
        await app.shutdown()
    except Exception as exc:
        logger.warning("Telegram app shutdown: %s", exc)