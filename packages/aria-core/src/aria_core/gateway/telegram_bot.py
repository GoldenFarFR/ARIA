from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
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
    try:
        from aria_core.relay_chat import log_message

        await log_message("aria", text)
    except Exception:  # noqa: BLE001 — le relais ne doit jamais impacter la réponse réelle
        pass


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
    import os
    commit = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "")[:12] or "unknown"
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
    from aria_core.gateway.x_twitter import (
        is_x_post_configured,
        is_x_read_configured,
        is_x_reading_active,
    )
    from aria_core.identity import official_x_at as x_at

    x_post = "connected ✅" if is_x_post_configured() else "missing keys"
    x_read = (
        "bearer ✅" if is_x_reading_active()
        else "coupée — bearer only" if is_x_read_configured()
        else "off"
    )
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
        f"Build commit: {commit}\n"
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
        f"Web: {str(getattr(settings, 'aria_web_search_provider', 'ddg') or 'ddg')}"
        f"{' (Tavily ✅)' if os.environ.get('TAVILY_API_KEY', '').strip() else ''}\n"
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
        # Module x_profile pas encore livré : garde défensive pour ne pas lever
        # ModuleNotFoundError si la commande est invoquée (parité avec le heartbeat).
        try:
            from aria_core.x_profile import (
                canonical_x_profile,
                fetch_live_x_profile,
                format_profile_summary,
                profile_fields_differ,
                sync_x_profile,
            )
        except ModuleNotFoundError:
            await _reply(message, "Profil X : module x_profile non livré (fonction à venir).")
            return

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
    from aria_core.locale import detect_operator_lang
    from aria_core.skills.github_skill import execute_github_sandbox

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "status").strip()
    lang = detect_operator_lang(text)

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

    if sub in ("repair", "fix", "corrige", "corriger"):
        # Correction operateur-only : edite le dernier commentaire showcase PR poste par ARIA
        # (le remplace par le message de relai correct qui te tague). Ne supprime rien.
        out, _ = await execute_github_sandbox("showcase pr repair", lang)
        await _reply(message, out)
        return

    await _reply(
        message,
        "Usage: /github status | list | create <nom> | delete <nom> | repair\n"
        "Ou en texte libre : supprime repo kikou",
    )


async def _handle_repertoire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return
    from aria_core.locale import detect_operator_lang
    from aria_core.skills.repertoire_skill import execute_manage_repertoire

    text = (message.text or "").strip()
    args = text.split()[1:] if " " in text else []
    sub = (args[0].lower() if args else "list").strip()
    lang = detect_operator_lang(text)

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
    """Incident #147 (12/07) : une photo envoyée SANS légende (pour tester la vision,
    par exemple) était traitée par défaut comme une demande de changement de photo de
    profil publique -- appliquée réellement sur le bot Telegram en prod (avatar réel
    remplacé par un portrait tiers, sans confirmation). Ce défaut datait d'avant
    l'existence de la vision (10/07, cf. docstring de _handle_photo) : à l'époque une
    photo sans légende n'avait qu'un seul sens possible. Ce n'est plus vrai -- une
    légende vide ou ambiguë doit désormais tomber sur la vision (lecture seule, sans
    conséquence publique), jamais sur un changement d'identité visuelle publique.
    Seul un signal EXPLICITE (/avatar, ou un mot-clé avatar sans ambiguïté) déclenche
    encore ce chemin."""
    text = (caption or "").strip()
    if not text:
        return False
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


def vision_enabled() -> bool:
    """Seam gaté OFF par défaut. Une image analysée coûte des tokens vision (LLM) à
    chaque envoi — jamais activé sans décision opérateur explicite."""
    import os

    return os.environ.get("ARIA_VISION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# Lecture directe d'une page web (13/07) -- même prudence que vision_enabled() :
# gate séparé (ARIA_WEB_FETCH_ENABLED, cf. knowledge/web_verify.py), admin-only.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Point d'entrée UNIQUE pour tout message photo (avant ce correctif du 10/07, AUCUN
    handler photo n'était enregistré — toute image envoyée à ARIA était ignorée en
    silence, y compris pour /avatar). Deux routes distinctes selon la légende :
      - mot-clé avatar EXPLICITE (``_caption_is_avatar_upload``) -> flux /avatar
        existant (photo de profil / identité visuelle publique) ;
      - légende vide, ambiguë, ou normale (une question, « juge cette situation »...)
        -> lecture visuelle (vision), gatée ``ARIA_VISION_ENABLED``, admin-only pour
        l'instant (coût LLM par image, pas encore ouvert au public).
    Incident #147 (12/07) : la légende vide déclenchait AUTREFOIS le changement
    d'avatar public par défaut -- corrigé, une photo ambiguë ne touche plus jamais
    l'identité visuelle publique sans signal explicite."""
    message = update.message
    if not message or not message.photo:
        return
    caption = (message.caption or "").strip()
    if _caption_is_avatar_upload(caption):
        await _handle_avatar_photo(update, context)
        return
    await _handle_vision_photo(update, context, caption)


async def _handle_vision_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, caption: str) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user:
        return

    if not is_admin(user.id):
        # Public : décision de scope volontaire (coût LLM par image, pas encore
        # ouvert). Décliner brièvement plutôt que le silence actuel — sans appel LLM.
        await _reply(
            message,
            "Je ne lis pas encore les images en dehors de l'équipe — envoie ta question en texte.",
        )
        return

    if not vision_enabled():
        await _reply(
            message,
            "L'analyse d'image n'est pas encore activée (ARIA_VISION_ENABLED désactivé).",
        )
        return

    import base64

    photo = message.photo[-1]
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001 — jamais planter sur un échec de téléchargement
        logger.info("vision: téléchargement photo échoué (%s)", exc)
        await _reply(message, "Je n'ai pas réussi à récupérer cette image, réessaie.")
        return

    image_data_uri = f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"
    prompt = caption or "Décris cette image et donne ta lecture."

    from aria_core.locale import LANG_FR, detect_operator_lang

    lang = detect_operator_lang(prompt) if caption else LANG_FR
    reply = await aria_brain._llm_response(prompt, lang, public=False, image_data_uri=image_data_uri)
    if reply is None:
        await _reply(message, "L'analyse d'image a échoué (LLM indisponible) — réessaie plus tard.")
        return
    await _reply(message, reply)


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
    from aria_core.locale import detect_operator_lang
    from aria_core.skills.github_skill import execute_github_sandbox

    lang = detect_operator_lang(body)
    prompt = f"create experiment sandbox {body}"
    out, _ = await execute_github_sandbox(prompt, lang)
    await _reply(message, out)


async def _handle_public_message(update: Update, text: str) -> None:
    """Courtesy + verified info only — no operator tools."""
    message = update.message
    user = update.effective_user
    if not message or not user:
        return

    if _URL_RE.search(text):
        # Décline systématiquement, indépendamment du gate ARIA_WEB_FETCH_ENABLED
        # (même posture que vision_enabled() pour les photos) -- jamais un fetch
        # discret d'une URL fournie par un visiteur public.
        await _reply(
            message,
            "Je ne lis pas encore de page web directement en dehors de l'équipe — "
            "pose ta question en texte.",
        )
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

    if re.match(r"^@claude\b", text, re.IGNORECASE):
        # Adressage explicite : message pour Claude Code, pas pour ARIA — n'active PAS
        # le pipeline LLM d'ARIA (elle ne doit pas répondre à la place de Claude). Déjà
        # journalisé tel quel dans le relais par process_webhook_update ; Claude le verra
        # à sa prochaine lecture (session VPS ou cloud) et répondra préfixé "🤖 Claude — ".
        await _reply(
            message,
            "📨 Message transmis à Claude (pas à moi) — il répondra ici, préfixé "
            "« 🤖 Claude — », dès sa prochaine lecture du relais.",
        )
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

    from aria_core.locale import detect_operator_lang

    lang = detect_operator_lang(text)

    url_match = _URL_RE.search(text)
    if url_match:
        from aria_core.knowledge.web_verify import answer_from_page, web_fetch_enabled

        if not web_fetch_enabled():
            await _reply(
                message,
                "La lecture de page web n'est pas encore activée "
                "(ARIA_WEB_FETCH_ENABLED désactivé)."
                if lang == "fr"
                else "Direct page reading is not enabled yet (ARIA_WEB_FETCH_ENABLED disabled).",
            )
            return

        await message.reply_chat_action("typing")
        url = url_match.group(0).rstrip(").,;!?»\"'")
        reply, _meta = await answer_from_page(url, text, lang=lang)
        if reply is None:
            reply = (
                "Je n'ai pas réussi à lire cette page pour répondre — réessaie ou reformule."
                if lang == "fr"
                else "I couldn't read that page to answer — try again or rephrase."
            )
        await _reply(message, reply)
        return

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
    from aria_core.skills.showcase_pr_watcher import wants_showcase_pr_repair

    if wants_showcase_pr_repair(text):
        # Correction operateur-only du dernier commentaire showcase PR (edition, tag operateur).
        await message.reply_chat_action("typing")
        try:
            out, _ = await execute_github_sandbox(text, lang)
            await _reply(message, out)
        except Exception as exc:
            logger.exception("Showcase PR repair failed")
            await _reply(message, f"Correction showcase : échec ({exc})")
        return

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

    from aria_core.dossier import build_dossier, extract_contract, format_dossier_telegram

    dossier_addr = extract_contract(text)
    if dossier_addr:
        # Un CA collé seul (ou dupliqué par copier-coller) → on sort le dossier daté
        # de ce token (toutes les analyses passées). Si vide, le rendu propose /vc | /scan.
        await message.reply_chat_action("typing")
        try:
            dossier = await build_dossier(dossier_addr)
            await _reply(message, format_dossier_telegram(dossier))
        except Exception as exc:
            logger.exception("Dossier token failed")
            await _reply(message, f"Dossier indisponible : {exc.__class__.__name__}")
        return

    await message.reply_chat_action("typing")
    try:
        response = await aria_brain.process(text, lang=lang, public_mode=False)
        reply_text = response.reply
        if response.data.get("llm_fallback_used") and is_owner(user.id):
            # #135 : signal opérationnel réservé au propriétaire, jamais à un simple admin
            # ni a fortiori au public -- silence total si Spark a répondu normalement.
            from aria_core.llm_economy import fallback_notice_line

            provider = response.data.get("llm_fallback_provider", "")
            reply_text = f"{reply_text}\n\n{fallback_notice_line(provider, lang=lang)}"
        await _reply(message, reply_text)
    except Exception:
        # #144 : traceback complet côté serveur (jamais affiché à l'opérateur) --
        # un crash intermittent, dépendant du contenu exact du message (cf. le bug
        # UnboundLocalError du 12/07), n'est diagnosticable qu'avec la stack complète
        # ET le texte qui l'a déclenché, pas juste le nom de la classe d'exception.
        logger.exception("Telegram brain.process failed on message: %r", text)
        await _reply(
            message,
            "Erreur interne — déjà journalisée côté serveur pour investigation.",
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

    if action == "vclang":
        await query.answer()
        lang, _, address = approval_id.partition(":")
        from aria_core.skills.vc_i18n import norm_lang

        lang = norm_lang(lang)
        if not address or not _SCAN_ADDR_RE.match(address):
            return
        message = query.message
        if not message:
            return
        try:
            await message.edit_reply_markup(reply_markup=None)  # retire les boutons, un seul choix possible
        except Exception:  # noqa: BLE001 — message déjà modifié/supprimé, jamais bloquant
            pass
        await _run_vc_analysis(message, address, test_mode=False, lang=lang)
        return

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

    # Menu "/" volontairement minimal (choix opérateur) : l'opérateur passe par la
    # conversation naturelle, jamais les slash-commandes. On n'expose donc dans le menu
    # QUE le kill-switch de sécurité (pause/reprise des actions sortantes). Toutes les
    # autres commandes (vc, scan, track, watchlist, cycles, status, langue, vcresult,
    # issue, thesis, theses, github, start, test_spend) restent ENREGISTRÉES et
    # fonctionnelles si on les tape — elles n'encombrent simplement plus le menu.
    commands = [
        BotCommand("stop", "⏸ Pause immédiate des actions sortantes (kill-switch)"),
        BotCommand("resume", "▶️ Reprendre les actions sortantes"),
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

    parts = body.split()
    address = parts[0].strip() if parts else ""
    flags = {p.strip().lower() for p in parts[1:]}
    include_smart_money = "smart" in flags
    include_fundamentals = "fond" in flags
    if not _SCAN_ADDR_RE.match(address):
        await _reply(
            message,
            "Usage : /scan <adresse_contrat> [smart] [fond]\n"
            "Adresse invalide — attendu : 0x suivi de 40 caractères hexadécimaux.",
        )
        return

    from aria_core.skills.acp_onchain_scan import scan_base_token

    if include_smart_money or include_fundamentals:
        await _reply(message, "⏳ Analyse approfondie en cours (plus lente)...")
    ctx = await scan_base_token(
        address, include_smart_money=include_smart_money, include_fundamentals=include_fundamentals
    )

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


# Garde de concurrence dédiée (#157) -- distincte de `_vc_semaphore` : un scan
# wallet multi-token peut prendre jusqu'à ~1-2 min (pagination Blockscout +
# jusqu'au plafond de tokens de `wallet_scoring_weights.WEIGHTS.max_tokens_analyzed`
# x GeckoTerminal throttlé), ne doit pas se disputer le même budget de
# concurrence que /vc.
_WALLET_SCORE_MAX_CONCURRENT = 2
_WALLET_SCORE_MAX_WAITERS = 4
_wallet_score_semaphore = asyncio.Semaphore(_WALLET_SCORE_MAX_CONCURRENT)
_wallet_score_waiters = 0


async def _run_wallet_score(message, addresses: list[str]) -> None:
    global _wallet_score_waiters
    if _wallet_score_semaphore.locked() and _wallet_score_waiters >= _WALLET_SCORE_MAX_WAITERS:
        await _reply(message, "⏳ File d'attente /walletscore pleine, réessaie dans quelques minutes.")
        return
    if _wallet_score_semaphore.locked():
        await _reply(message, "⏳ Une autre analyse wallet est en cours, mise en file d'attente...")

    _wallet_score_waiters += 1
    try:
        await _wallet_score_semaphore.acquire()
    finally:
        _wallet_score_waiters -= 1
    try:
        await _wallet_score_analyze_and_reply(message, addresses)
    finally:
        _wallet_score_semaphore.release()


def _format_wallet_score_card(card) -> list[str]:
    from aria_core.services.wallet_scoring_weights import WEIGHTS

    lines = [f"\n— {card.address}" + (f" ({card.display_name})" if card.display_name else "")]
    if not card.available:
        lines.append(f"  Indisponible : {card.error}")
        return lines
    if card.disqualified:
        lines.append("  🔴 DISQUALIFIÉ : " + "; ".join(card.disqualification_reasons))
    if card.financing_check_note:
        lines.append(f"  ⚠️ {card.financing_check_note}")
    # Transparence multi-chaînes (#157, 14/07) : jamais laisser penser qu'ARIA
    # a "tout" vu par défaut -- montre explicitement où une activité a été trouvée.
    chains_label = {"base": "Base", "ethereum": "Ethereum", "bnb": "BNB Chain"}
    scanned = ", ".join(chains_label.get(c, c) for c in card.chains_scanned) or "aucune"
    lines.append(f"  Chaînes avec activité trouvée : {scanned}")
    lines.append(
        f"  Tokens analysés : {card.tokens_analyzed}/{card.tokens_found}"
        + (f" (plafond de {WEIGHTS.max_tokens_analyzed} atteint)" if card.tokens_skipped_capped else "")
    )
    if card.unpriced_legs or card.pool_lookup_errors:
        # Diagnostic (#157, 14/07) : distingue "pool jamais trouvé sur GeckoTerminal"
        # (token trop obscur/mort, pas un bug) d'un autre problème de valorisation --
        # jamais deviner en silence pourquoi la valorisation est vide.
        lines.append(
            f"  Diagnostic prix : {card.unpriced_legs} jambe(s) sans prix, "
            f"{card.pool_lookup_errors} token(s) sans pool GeckoTerminal résolu"
        )
    lines.append(f"  Win rate : {card.win_rate:.0%}" if card.win_rate is not None else "  Win rate : indisponible")
    lines.append(
        f"  PnL réalisé : ${card.realized_pnl_usd:,.2f}"
        if card.realized_pnl_usd is not None
        else "  PnL réalisé : indisponible"
    )
    lines.append(
        f"  Sortino : {card.sortino:.2f}" if card.sortino is not None else "  Sortino : indisponible"
    )
    lines.append(f"  Récurrence entrée précoce (multi-lancements) : {card.early_entry_recurrence_count} token(s)")
    if card.suspect_positive:
        lines.append("  🟢 Suspect positif (exceptionnel sur plusieurs axes à la fois) — à surveiller de près.")
    if card.thesis:
        lines.append(f"  Thèse : {card.thesis}")
    return lines


async def _wallet_score_analyze_and_reply(message, addresses: list[str]) -> None:
    from aria_core.services.geckoterminal import geckoterminal_client
    from aria_core.services.goplus import goplus_client
    from aria_core.services.smart_money import score_wallets

    # ``chains``/``client`` omis : score_wallets utilise le vrai registre
    # multi-chaînes de production (Base/Ethereum/BNB, #157 14/07) -- une même
    # adresse 0x est valide sur toutes, ARIA essaie chaque chaîne et consolide.
    report = await score_wallets(addresses, gecko=geckoterminal_client, goplus=goplus_client)

    if not report.available:
        await _reply(message, f"⚠️ {report.error or 'analyse indisponible'}")
        return

    lines = ["🕵️ Évaluation smart-wallet — confirmation/contexte, JAMAIS un signal de copy-trade."]
    for card in report.wallets:
        lines.extend(_format_wallet_score_card(card))

    if report.convergence_pairs:
        lines.append("\n⚠️ Wallets soumis ensemble partageant une source de financement (suspects même entité) :")
        lines.extend(f"  {a} <-> {b}" for a, b in report.convergence_pairs)

    if report.synthesis:
        lines.append(f"\nSynthèse : {report.synthesis}")

    await _reply(message, "\n".join(lines))


async def _handle_walletscore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/walletscore <a1> [a2] [a3] — évaluateur "smart wallet" maison (#157), lecture
    seule : 1 à 3 adresses de WALLET (pas un contrat token, cf. /scan pour ça) ->
    disqualifiants durs, score composite (PnL/win-rate FIFO, Sortino, récurrence
    d'entrée précoce multi-lancements, diversification, drawdown), drapeau "suspect
    positif" séparé, thèse LLM. Toujours une confirmation/contexte, jamais un
    déclencheur. Gate ``ARIA_WALLET_SCORING_ENABLED``, OFF par défaut."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.services.smart_money import wallet_scoring_enabled

    if not wallet_scoring_enabled():
        await _reply(message, "Évaluateur wallet désactivé (ARIA_WALLET_SCORING_ENABLED).")
        return

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    addresses = [p.strip() for p in body.split() if p.strip()]
    if not addresses or len(addresses) > 3 or not all(_SCAN_ADDR_RE.match(a) for a in addresses):
        await _reply(
            message,
            "Usage : /walletscore <adresse_wallet> [adresse2] [adresse3]\n"
            "1 à 3 adresses WALLET (pas un contrat token) — attendu : 0x suivi de 40 caractères hexadécimaux.",
        )
        return

    await _reply(message, "⏳ Analyse wallet en cours (peut prendre jusqu'à quelques minutes)...")
    await _run_wallet_score(message, addresses)


def _format_judge_verdict(v, lang: str = "fr") -> str:
    """Formatte le verdict du proof engine (juge) pour Telegram.

    Le texte du juge est déjà sanitisé en amont (``vc_judge`` neutralise les
    chevrons) — on se contente de le mettre en forme. ``lang`` (fr/en) ne traduit
    que les libellés fixes ; les codes verdict/reco du juge restent inchangés.
    """
    from aria_core.skills.vc_i18n import judge_strings

    j = judge_strings(lang)
    emoji = {"solide": "🟢", "fragile": "🟡", "rejeté": "🔴"}.get(v.verdict, "⚪")
    src = j["src_llm"] if v.llm_used else j["src_det"]
    rr_ok = j["yes"] if v.coherence_rr else j["no"]
    lines = [
        j["header"].format(src=src),
        f"{emoji} {j['verdict']} : {v.verdict} · {j['score']} {v.score}/10"
        f" · {j['rr_ok']} : {rr_ok}",
        f"{j['reco']} : {v.recommandation_juge}",
    ]
    if v.resume:
        lines += ["", v.resume]
    if v.points_forts:
        lines += ["", j["strengths"]] + [f"• {p}" for p in v.points_forts[:5]]
    if v.points_faibles:
        lines += ["", j["weaknesses"]] + [f"• {p}" for p in v.points_faibles[:5]]
    if v.claims_non_etayes:
        lines += ["", j["unsupported"]] + [f"• {c}" for c in v.claims_non_etayes[:5]]
    return "\n".join(lines)


# Garde de concurrence — plafonne les analyses VC simultanées. Chaque /vc lance
# un scan on-chain + un appel LLM lourd (analyse, et en test le juge). Pour une
# boutique 4-5 clients, 3 en parallèle suffit ; au-delà on met en file, et on
# refuse poliment quand la file est pleine (évite l'empilement mémoire et la
# surcharge du fournisseur LLM). Le sémaphore protège aussi contre un burst de
# commandes /vc rapprochées.
_VC_MAX_CONCURRENT = 3
_VC_MAX_WAITERS = 6
_vc_semaphore = asyncio.Semaphore(_VC_MAX_CONCURRENT)
_vc_waiters = 0


_VC_LANG_BUTTONS = (("fr", "🇫🇷 Français"), ("en", "🇬🇧 English"))


async def _run_vc_analysis(message, address: str, *, test_mode: bool, lang: str) -> None:
    """Acquiert le sémaphore de concurrence puis lance l'analyse. Partagé par le
    mode test (direct) et le chemin d'envoi réel (déclenché après choix de langue)."""
    global _vc_waiters
    from aria_core.skills.vc_i18n import scaffold_strings

    s = scaffold_strings(lang)
    if _vc_semaphore.locked() and _vc_waiters >= _VC_MAX_WAITERS:
        await _reply(message, s["overloaded"])
        return
    if _vc_semaphore.locked():
        await _reply(message, s["busy"])

    _vc_waiters += 1
    try:
        await _vc_semaphore.acquire()
    finally:
        _vc_waiters -= 1
    try:
        await _vc_analyze_and_reply(message, address, test_mode=test_mode, lang=lang, s=s)
    finally:
        _vc_semaphore.release()


async def _handle_vc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vc <adresse> [test] — analyse VC complète : ordre court ici, rapport détaillé par email.

    Lecture seule + proposition. Aucune exécution : l'ordre est signé manuellement
    sur Tangem par l'opérateur.

    MODE TEST admin — `/vc <adresse> test` : l'analyse tourne et le raisonnement
    complet est affiché ici, mais AUCUN email n'est envoyé et AUCUNE prédiction
    n'est enregistrée dans le track-record (compteurs inchangés). Pour tester sans
    polluer les stats ni spammer. Utilise directement la préférence `/langue`
    (pas de question interactive — outil de debug rapide pour l'opérateur).

    Chemin d'envoi réel (hors test) : avant de lancer l'analyse, ARIA demande la
    langue du rapport (boutons) — jamais l'adresse email (destinataire fixe,
    `ARIA_VC_REPORT_TO`), jamais de confirmation d'envoi séparée. Le choix de
    langue déclenche directement l'analyse + l'envoi via le callback ``vclang``.
    Concurrence bornée par ``_vc_semaphore`` (voir ci-dessus).
    """
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.skills import vc_prefs
    from aria_core.skills.vc_i18n import scaffold_strings

    lang = await vc_prefs.get_output_lang()
    s = scaffold_strings(lang)

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    parts = body.split()
    # Flag `test` en fin d'arguments (insensible à la casse) — réservé à l'admin
    # (le handler entier est déjà admin-gated ci-dessus).
    test_mode = len(parts) >= 2 and parts[-1].lower() == "test"
    address = parts[0].strip() if parts else ""
    if not _SCAN_ADDR_RE.match(address):
        await _reply(message, s["usage"])
        return

    if test_mode:
        await _run_vc_analysis(message, address, test_mode=True, lang=lang)
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"vclang:{code}:{address}")
        for code, label in _VC_LANG_BUTTONS
    ]])
    await message.reply_text(
        "🌐 Langue du rapport ?" if lang == "fr" else "🌐 Report language?",
        reply_markup=keyboard,
    )


async def _vc_analyze_and_reply(message, address: str, *, test_mode: bool, lang: str, s: dict) -> None:
    """Cœur de l'analyse /vc, exécuté sous le sémaphore de concurrence.

    Séparé de ``_handle_vc`` pour que la garde de concurrence enveloppe l'intégralité
    du travail lourd (scan + LLM + juge + email) dans un unique try/finally.
    """
    import os
    from datetime import datetime, timezone

    from aria_core import vc_predictions
    from aria_core.skills.vc_analysis import analyze_vc, analyze_vc_with_context, format_telegram_order
    from aria_core.skills.vc_delivery import send_vc_report
    from aria_core.skills.vc_report import report_integrity

    await _reply(message, s["analyzing"])
    if test_mode:
        # Mode test : on récupère aussi le contexte de scan pour l'audit du juge.
        result, ctx = await analyze_vc_with_context(address, lang=lang)
    else:
        result = await analyze_vc(address, lang=lang)
        ctx = None
    capital_raw = os.environ.get("ARIA_CAPITAL_USD", "").strip()
    try:
        capital_usd = float(capital_raw) if capital_raw else None
    except ValueError:
        capital_usd = None
    order_text = format_telegram_order(result, capital_usd=capital_usd, lang=lang)
    await _reply(message, order_text)

    from aria_core import repertoire_db
    from aria_core.skills.vc_session_context import queue_video_candidate, record_operator_vc

    try:
        await repertoire_db.save_message("user", f"/vc {address}", skill_used="vc")
        await repertoire_db.save_message("agent", order_text, skill_used="vc")
    except Exception as exc:  # noqa: BLE001 — historique best-effort, jamais bloquant pour le rapport
        logger.warning("save_message /vc échoué: %s", exc)

    if test_mode:
        # MODE TEST : on affiche le raisonnement complet mais on n'envoie aucun
        # email et on n'écrit rien dans le track-record (compteurs inchangés).
        rapport = result.rapport_detaille or s["no_reasoning"]
        # Tronque proprement avec un marqueur ; _reply plafonne ensuite à 4000.
        limit = 3900
        if len(rapport) > limit:
            rapport = rapport[:limit].rstrip() + s["test_truncated"]
        await _reply(message, s["test_reasoning"] + rapport)
        # Proof engine (#2) — le juge adverse audite l'analyse sur les MÊMES faits
        # on-chain. Mode test admin uniquement : aucun coût/latence ajouté au flux
        # client, c'est un outil de contrôle qualité pour l'opérateur.
        try:
            from aria_core.skills.vc_judge import judge_analysis

            verdict = await judge_analysis(result, ctx, lang=lang)
            await _reply(message, _format_judge_verdict(verdict, lang=lang))
        except Exception as exc:  # noqa: BLE001 — l'audit ne doit jamais casser le mode test
            logger.warning("proof engine (juge) échoué: %s", exc)
        await record_operator_vc(result, prediction_id=None, telegram_summary=order_text)
        try:
            await queue_video_candidate(result)
        except Exception as exc:  # noqa: BLE001 — capture vidéo best-effort, jamais bloquante
            logger.warning("vc video snapshot (test mode) échoué: %s", exc)
        await _reply(message, s["test_footer"])
        return

    tier = (os.environ.get("ARIA_REPORT_TIER") or "premium").strip().lower() or "premium"

    # Auto-log de la prédiction (shadow) — construit le track record de pertinence.
    generated_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    ref_id, _ = report_integrity(result, generated_at=generated_at)
    report_number = None
    series_number = None
    try:
        report_number = await vc_predictions.count_predictions_for_contract(result.contract) + 1
        series_number = await vc_predictions.total_predictions_count() + 1
        pred_id = await vc_predictions.record_prediction(
            contract=result.contract,
            recommandation=result.recommandation,
            potentiel=result.potentiel,
            risque=result.risque,
            taille_pct=result.taille_pct,
            security_score=result.security_score,
            llm_used=result.llm_used,
            report_ref=ref_id,
        )
        await record_operator_vc(result, prediction_id=pred_id, telegram_summary=order_text)
        try:
            await queue_video_candidate(result)
        except Exception as exc:  # noqa: BLE001 — capture vidéo best-effort, jamais bloquante
            logger.warning("vc video snapshot échoué: %s", exc)
        await _reply(message, f"🗃️ Prédiction #{pred_id} enregistrée. Clôture plus tard : /vcresult {pred_id} <pnl%> [note].")
    except Exception as exc:  # noqa: BLE001 — le log ne doit jamais casser l'analyse
        logger.warning("vc auto-log échoué: %s", exc)

    # Rapport détaillé par email (sous kill-switch, dégradation sûre si SMTP absent).
    email_ok, email_error = await send_vc_report(
        result,
        generated_at=generated_at,
        report_number=report_number,
        series_number=series_number,
        capital_usd=capital_usd,
        tier=tier,
        lang=lang,
    )
    if email_ok:
        await _reply(message, "📧 Rapport détaillé envoyé par email.")
    else:
        await _reply(message, f"📧 Rapport email non envoyé : {email_error}")


async def _handle_vcresult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vcresult <id> <pnl%> [note] — attribue un résultat réel à une prédiction VC."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    usage = (
        "Usage : /vcresult <id> <pnl%> [note]\n"
        "Ex : /vcresult 3 +18 catalyseur listing.\n"
        "Le pnl% est le résultat réel de la prédiction (positif ou négatif)."
    )
    parts = body.split(maxsplit=2)
    if len(parts) < 2 or not parts[0].isdigit():
        await _reply(message, usage)
        return

    pred_id = int(parts[0])
    try:
        outcome_pct = float(parts[1].replace("%", "").replace(",", ".").lstrip("+"))
    except ValueError:
        await _reply(message, "pnl% invalide (attendu un nombre, ex. +18 ou -25).\n\n" + usage)
        return
    note = parts[2].strip() if len(parts) > 2 else ""

    from aria_core import vc_predictions

    closed = await vc_predictions.close_prediction(pred_id, outcome_pct=outcome_pct, note=note)
    if closed is None:
        await _reply(message, f"Prédiction #{pred_id} introuvable ou déjà clôturée.")
        return
    await _reply(
        message,
        f"✅ Prédiction #{pred_id} clôturée — {closed['recommandation']} → résultat {outcome_pct:+.1f}%.",
    )


async def _handle_langue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/langue [fr|en] — langue de sortie des analyses VC (mémorisée). Sans argument : affiche l'actuelle."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.skills import vc_prefs
    from aria_core.skills.vc_i18n import scaffold_strings

    text = (message.text or "").strip()
    parts = text.split()
    arg = parts[1].strip().lower() if len(parts) >= 2 else ""
    if not arg and context.args:
        arg = " ".join(context.args).strip().lower()

    if not arg:
        current = await vc_prefs.get_output_lang()
        await _reply(message, scaffold_strings(current)["lang_current"].format(lang=current))
        return

    try:
        new_lang = await vc_prefs.set_output_lang(arg)
    except ValueError:
        current = await vc_prefs.get_output_lang()
        await _reply(message, scaffold_strings(current)["lang_usage"])
        return

    await _reply(message, scaffold_strings(new_lang)["lang_set"])


async def _handle_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/track — mesure de pertinence : hit-rate, P&L moyen, calibration."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core import vc_predictions

    m = await vc_predictions.metrics()
    if m["total"] == 0:
        await _reply(message, "Aucune prédiction enregistrée. Lance des analyses avec /vc.")
        return

    lines = [
        "📈 Pertinence ARIA — track record VC",
        f"Prédictions : {m['total']} ({m['closed']} clôturées, {m['open']} ouvertes)",
    ]
    if m["hit_rate"] is not None:
        lines.append(f"Hit-rate BUY : {m['hit_rate']:.0%} sur {m['buy_count']} BUY clôturés")
        lines.append(f"P&L moyen BUY : {m['avg_pnl_buy']:+.1f}%")
    else:
        lines.append("Pas encore de BUY clôturé — clôture avec /vcresult pour mesurer.")

    if m["calibration"]:
        lines.append("")
        lines.append("Calibration (Potentiel → P&L moyen réel) :")
        for b in m["calibration"]:
            lines.append(f"  {b['bucket']}/10 : {b['avg_pnl']:+.1f}% (n={b['count']})")
        lines.append("Idéal : le P&L croît avec le potentiel.")

    await _reply(message, "\n".join(lines))


async def _handle_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist [n] — checklist des contrats qu'ARIA suit de près : le pool screené
    classé par score composite (sécurité + liquidité + concentration + verdict).

    Priorité de lecture, jamais un ordre — pour voir CE sur quoi ARIA garde l'œil
    avant l'analyse VC approfondie (/vc <adresse>)."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
    try:
        n = max(1, min(30, int(arg))) if arg else 10
    except ValueError:
        n = 10

    from aria_core.skills.candidate_ranking import top_candidates

    tops = await top_candidates(n)
    if not tops:
        await _reply(
            message,
            "Pool de surveillance vide pour l'instant — aucun contrat suivi actuellement.",
        )
        return

    lines = [f"👀 Contrats suivis de près ({len(tops)}/{n} demandés) :", ""]
    for i, c in enumerate(tops, start=1):
        name = c.symbol or f"{c.contract[:6]}…{c.contract[-4:]}"
        lines.append(
            f"{i}. {name} — score {c.rank_score:.0f} · sécurité {c.security_score} · "
            f"liq ${c.liquidity_usd:,.0f} · {c.verdict}"
        )
        lines.append(f"   {c.contract}")
    lines.append("")
    lines.append("Classement de priorité (jamais un ordre) — analyse complète : /vc <adresse>")
    await _reply(message, "\n".join(lines))


async def _handle_cycles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cycles — les 3 derniers cycles Bitcoin (halving à halving) : accumulation,
    hausse, distribution, baisse, chiffres réels + lecture qualitative. Contexte macro
    long terme, jamais un signal d'achat/vente."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.skills.btc_cycles import analyze_btc_cycles, format_cycles_report

    await _reply(message, "Analyse des cycles Bitcoin en cours (historique réel, ça peut prendre un instant)…")
    result = await analyze_btc_cycles()
    await _reply(message, format_cycles_report(result))


async def _handle_readiness(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/feuvert — scorecard objectif contre les 8 cases pré-engagées de
    docs/protocole-argent-reel.md avant tout argent réel. Jamais un jugement
    subjectif : calculé depuis le vrai journal vc_predictions. Admin-only —
    l'état du feu vert argent réel n'a rien à faire en surface publique."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.skills.real_money_readiness import (
        compute_readiness_scorecard,
        format_readiness_report,
    )

    scorecard = await compute_readiness_scorecard()
    await _reply(message, format_readiness_report(scorecard))


async def _handle_sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sentiment — dernière lecture de sentiment (RSI/Bollinger/momentum/retracement,
    vocabulaire Wall St Cheat Sheet) des paires principales. Lit ce que le cycle
    heartbeat `market_sentiment_cycle` a déjà calculé — ne recalcule rien ici."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core.skills.market_sentiment import format_sentiment_report, latest_readings

    readings = await latest_readings()
    await _reply(message, format_sentiment_report(readings))


async def _handle_thesis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/these <adresse> <BUY|WATCH|SELL|AVOID> <thèse...> — journalise un pari (aucune exécution)."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    from aria_core import investment_memory

    parts = body.split(maxsplit=2)
    usage = (
        "Usage : /these <adresse> <BUY|WATCH|SELL|AVOID> <thèse>\n"
        "Ex : /these 0xabc... WATCH holders solides, liquidité faible à surveiller.\n"
        "Journal de raisonnement uniquement — aucune exécution de trade."
    )
    if len(parts) < 3:
        await _reply(message, usage)
        return

    address, decision_raw, thesis = parts[0].strip(), parts[1].strip().upper(), parts[2].strip()
    if not _SCAN_ADDR_RE.match(address):
        await _reply(message, "Adresse invalide — attendu : 0x suivi de 40 caractères hexadécimaux.\n\n" + usage)
        return
    if decision_raw not in investment_memory.VALID_DECISIONS:
        await _reply(
            message,
            f"Décision invalide : {decision_raw}. Attendu : {', '.join(investment_memory.VALID_DECISIONS)}.",
        )
        return

    thesis_id = await investment_memory.record_thesis(
        token_address=address, thesis=thesis, decision=decision_raw
    )
    await _reply(
        message,
        f"📝 Thèse #{thesis_id} enregistrée — {decision_raw} sur {address}.\n"
        f"Clôture plus tard avec : /issue {thesis_id} <résultat> | <leçon>.",
    )


async def _handle_issue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/issue <id> <résultat/P&L> | <leçon> — clôture une thèse et attribue son issue."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    usage = (
        "Usage : /issue <id> <résultat/P&L> | <leçon>\n"
        "Ex : /issue 3 +18% en 2 semaines | j'ai sous-estimé le catalyseur listing.\n"
        "Le séparateur « | » distingue le résultat de la leçon (leçon optionnelle)."
    )
    parts = body.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await _reply(message, usage)
        return

    thesis_id = int(parts[0])
    rest = parts[1].strip()
    if "|" in rest:
        outcome, lesson = (segment.strip() for segment in rest.split("|", 1))
    else:
        outcome, lesson = rest, ""

    from aria_core import investment_memory

    closed = await investment_memory.close_thesis(thesis_id, outcome=outcome, lesson=lesson)
    if closed is None:
        await _reply(
            message,
            f"Thèse #{thesis_id} introuvable ou déjà clôturée — aucune modification.",
        )
        return

    lines = [
        f"✅ Thèse #{thesis_id} clôturée ({closed['decision']} sur {closed['token_address']}).",
        f"Résultat : {outcome}",
    ]
    if lesson:
        lines.append(f"Leçon : {lesson}")
    await _reply(message, "\n".join(lines))


_DIRECTIVE_STATUS_ICON = {
    "pending": "⏳",
    "executing": "⚙️",
    "done": "✅",
    "refused": "🚫",
}


async def _handle_aria_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/canal — surface de contrôle du canal ARIA -> Claude Code (admin, pilote).

    Nom délibérément DISTINCT de `/directive` : une ancienne commande opérateur
    portait ce nom (règle permanente -> ARIA via `directives.append_directive`) ;
    une collision de nom de FONCTION Python (même nom `_handle_directive` que ce
    pilote) l'a écrasée silencieusement une fois (10/07). Cette ancienne commande a
    depuis été retirée (jamais utilisée en pratique, doublon du vrai flux : demander
    à Claude Code d'éditer `directives.md` directement, revu et testé). Garder `/canal`
    sous un nom distinct reste la bonne pratique anti-collision, même sans doublon actif.

    Sous-commandes :
      /canal list                       — la file (en cours + traitées)
      /canal log                        — le journal d'audit (append-only)
      /canal propose <cat> <titre>      — dépose une directive (cat: repo_hygiene|docs|backlog)
      /canal halt [raison]              — coupe-circuit (fige le canal)
      /canal resume                     — lève le coupe-circuit

    Ne DÉCLENCHE aucune exécution : la file est lue et traitée par une session Claude
    Code côté VPS. Le canal reste gaté OFF (ARIA_DIRECTIVE_CHANNEL_ENABLED) tant qu'il
    n'est pas explicitement activé.
    """
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core import aria_directives as ad

    text = (message.text or "").strip()
    body = text.split(maxsplit=1)[1].strip() if " " in text else ""
    if not body and context.args:
        body = " ".join(context.args).strip()

    parts = body.split(maxsplit=1)
    sub = parts[0].strip().lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    usage = (
        "Canal de directives ARIA -> Claude Code (pilote) :\n"
        "/canal list — la file\n"
        "/canal log — le journal d'audit\n"
        "/canal propose <cat> <titre> — cat: repo_hygiene | docs | backlog\n"
        "/canal halt [raison] — coupe-circuit\n"
        "/canal resume — lever le coupe-circuit"
    )

    if sub in ("help", "aide", "?"):
        await _reply(message, usage)
        return

    if sub == "propose":
        cat_parts = rest.split(maxsplit=1)
        if len(cat_parts) < 2:
            await _reply(message, "Usage : /canal propose <cat> <titre>\n" + usage)
            return
        category, title = cat_parts[0].strip().lower(), cat_parts[1].strip()
        res = await ad.propose_directive(category, title)
        if res.get("ok"):
            await _reply(message, f"⏳ Directive #{res['id']} déposée ({category}) : {title}")
        else:
            await _reply(message, f"🚫 Refusée : {res.get('reason', 'inconnue')}")
        return

    if sub == "halt":
        await ad.halt_channel(rest or "halt manuel Telegram")
        await _reply(message, "🛑 Canal FIGÉ. Aucune directive ne sera traitée. /canal resume pour reprendre.")
        return

    if sub == "resume":
        await ad.resume_channel()
        state = "OFF" if not ad.channel_enabled() else "ON"
        await _reply(message, f"▶️ Coupe-circuit levé. (Gate ARIA_DIRECTIVE_CHANNEL_ENABLED : {state}.)")
        return

    if sub == "log":
        entries = await ad.read_log(limit=15)
        if not entries:
            await _reply(message, "Journal vide.")
            return
        lines = ["🧾 Journal (récent -> ancien) :"]
        for e in entries:
            did = f"#{e['directive_id']}" if e["directive_id"] else "—"
            lines.append(f"{e['at'][11:19]} · {e['actor']} · {e['event']} {did} {e['detail'][:60]}")
        await _reply(message, "\n".join(lines))
        return

    # défaut : list
    directives = await ad.list_directives(limit=30)
    halted = "🛑 FIGÉ" if ad.is_halted() else "actif"
    gate = "ON" if ad.channel_enabled() else "OFF"
    if not directives:
        await _reply(message, f"File vide. (Canal : {halted} · gate : {gate}.)\n\n{usage}")
        return
    lines = [f"📋 File de directives (canal {halted} · gate {gate}) :"]
    for d in directives:
        icon = _DIRECTIVE_STATUS_ICON.get(d["status"], "•")
        lines.append(f"{icon} #{d['id']} [{d['category']}] {d['title']}")
        if d["status"] in ("done", "refused") and d["outcome"]:
            lines.append(f"    -> {d['outcome'][:80]}")
    await _reply(message, "\n".join(lines))


async def _handle_theses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/theses — liste les thèses encore ouvertes (résultat non attribué)."""
    if not await _admin_check_reply(update):
        return
    message = update.message
    if not message:
        return

    from aria_core import investment_memory

    open_theses = await investment_memory.list_open_theses()
    if not open_theses:
        await _reply(message, "Aucune thèse ouverte. Enregistre-en une avec /these.")
        return

    lines = ["📒 Thèses ouvertes :"]
    for row in open_theses:
        lines.append(f"#{row['id']} — {row['decision']} {row['token_address']}\n  {row['thesis']}")
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
    app.add_handler(CommandHandler("walletscore", _handle_walletscore))
    app.add_handler(CommandHandler("vc", _handle_vc))
    app.add_handler(CommandHandler("vcresult", _handle_vcresult))
    app.add_handler(CommandHandler("track", _handle_track))
    app.add_handler(CommandHandler("watchlist", _handle_watchlist))
    app.add_handler(CommandHandler("cycles", _handle_cycles))
    app.add_handler(CommandHandler("feuvert", _handle_readiness))
    app.add_handler(CommandHandler("sentiment", _handle_sentiment))
    app.add_handler(CommandHandler(["langue", "lang", "language"], _handle_langue))
    app.add_handler(CommandHandler("these", _handle_thesis))
    app.add_handler(CommandHandler("issue", _handle_issue))
    app.add_handler(CommandHandler("theses", _handle_theses))
    app.add_handler(CommandHandler("github", _handle_github))
    app.add_handler(CommandHandler("canal", _handle_aria_channel))

    # Inline keyboard buttons (approve/reject/explain — approvals + wallet spend flow)
    app.add_handler(CallbackQueryHandler(_handle_callback))

    # Photos (avatar upload OR vision analysis, dispatched by caption — cf. _handle_photo).
    # Avant ce correctif, AUCUN handler photo n'était enregistré : toute image envoyée à
    # ARIA était ignorée en silence.
    app.add_handler(MessageHandler(filters.PHOTO, _handle_photo))

    # All other interactions via plain text (no slash commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))


# Garde-fou anti-boucle : Telegram redélivre le MÊME update (même update_id)
# tant qu'il ne reçoit pas un 200 rapide. On mémorise les derniers update_id
# traités pour ne jamais relancer deux fois la même analyse, même en cas de
# redélivrance (backlog webhook, retries). Le check-and-set est synchrone donc
# atomique vis-à-vis de la boucle asyncio (aucun await entre lecture et écriture).
_seen_update_ids: "OrderedDict[int, None]" = OrderedDict()
_SEEN_UPDATE_CAP = 1024


def _webhook_update_already_seen(update_id: int | None) -> bool:
    if update_id is None:
        return False
    if update_id in _seen_update_ids:
        return True
    _seen_update_ids[update_id] = None
    while len(_seen_update_ids) > _SEEN_UPDATE_CAP:
        _seen_update_ids.popitem(last=False)
    return False


async def process_webhook_update(payload: dict) -> None:
    if not _bot_app:
        raise RuntimeError("Bot not initialized")
    from telegram import Update

    update_id = payload.get("update_id") if isinstance(payload, dict) else None
    if _webhook_update_already_seen(update_id):
        logger.info("Telegram webhook: update %s déjà traité — ignoré (anti-boucle)", update_id)
        return

    try:
        text = (payload.get("message") or {}).get("text") if isinstance(payload, dict) else None
        if text:
            from aria_core.relay_chat import log_message

            await log_message("operator", text)
    except Exception:  # noqa: BLE001 — le relais ne doit jamais impacter le traitement réel
        pass

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