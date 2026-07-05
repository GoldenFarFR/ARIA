"""Garde-fou dépenses ACP — escalade Telegram obligatoire avant toute exécution financière.

Chemin structurellement séparé de ``telegram_bot.request_approval`` : ce module ne
référence JAMAIS ``settings.aria_autonomous`` — les dépenses restent gardées quel que
soit le mode d'autonomie général. ``escalate_spend`` ne fait que créer les
enregistrements et notifier Telegram ; elle n'appelle jamais ``acp_cli``. L'exécution
réelle vit exclusivement dans ``resolve_spend``, atteignable uniquement depuis un
clic Telegram réel (``telegram_bot._handle_callback``). Si l'escalade ne peut pas être
délivrée, l'action reste bloquée en attente — aucune dépense n'a lieu.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from aria_core import outgoing_pause
from aria_core.approvals import create_approval
from aria_core.memory import append_memory
from aria_core.skills.acp_cli import client_fund_job, trade_tokens
from aria_core.wallet_ledger import claim_for_decision, create_ledger_entry, set_result

logger = logging.getLogger(__name__)


def _exec_client_fund_job(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    return client_fund_job(
        payload["job_id"],
        amount_usdc=payload.get("amount_usdc"),
        chain_id=payload.get("chain_id", "8453"),
    )


def _exec_trade_tokens(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    return trade_tokens(
        token_in=payload["token_in"],
        token_out=payload["token_out"],
        amount_in=payload["amount_in"],
        chain_in=payload.get("chain_in", "8453"),
        chain_out=payload.get("chain_out", "8453"),
        slippage=payload.get("slippage", ""),
    )


WALLET_ACTIONS: dict[str, Callable[[dict[str, Any]], tuple[dict | None, str | None]]] = {
    "client_fund_job": _exec_client_fund_job,
    "trade_tokens": _exec_trade_tokens,
}


class SpendEscalationError(RuntimeError):
    """Levée quand l'escalade Telegram n'a pas pu être délivrée — aucune dépense n'a lieu."""


async def escalate_spend(
    action: str,
    *,
    amount: str,
    counterparty: str,
    description: str,
    payload: dict[str, Any],
) -> str:
    """Crée l'approbation + le ledger et envoie le prompt Telegram 3 options.

    N'appelle jamais l'exécuteur ACP — l'exécution ne se produit que dans
    ``resolve_spend``, déclenchée uniquement par un clic Telegram réel. Si l'envoi
    échoue, l'entrée reste ``pending`` indéfiniment : aucune dépense n'a lieu.
    """
    # Kill-switch (fail-closed pour l'argent) : en pause OU si l'état est illisible/corrompu,
    # on ne crée même pas l'escalade. Les appelants (acp_client_actions, _handle_test_spend)
    # catchent déjà SpendEscalationError et affichent le message.
    _spend_block = outgoing_pause.money_block_reason("Cette dépense")
    if _spend_block:
        raise SpendEscalationError(_spend_block)
    if action not in WALLET_ACTIONS:
        raise ValueError(f"Action de dépense inconnue : {action}")

    payload_json = json.dumps(payload, ensure_ascii=False)
    req = await create_approval(action=f"spend:{action}", description=description, payload=payload_json)
    await create_ledger_entry(
        entry_id=req.id,
        action=action,
        amount=amount,
        counterparty=counterparty,
        payload=payload_json,
    )

    try:
        await send_spend_prompt(req.id, action, description)
    except Exception as exc:
        logger.error("Escalade Telegram échouée pour spend #%s (%s): %s", req.id, action, exc)
        await set_result(req.id, f"telegram_send_failed: {exc}")
        append_memory(
            "wallet",
            f"[BLOQUÉ] Escalade Telegram échouée — {action} {amount} / {counterparty} "
            f"(#{req.id}) — AUCUNE dépense effectuée : {exc}",
        )
        raise SpendEscalationError(
            f"Échec de la notification Telegram pour l'action #{req.id} — action bloquée, "
            "aucune dépense effectuée."
        ) from exc

    append_memory(
        "wallet",
        f"[EN ATTENTE] {action} {amount} / {counterparty} (#{req.id}) — escalade Telegram envoyée.",
    )
    return req.id


async def send_spend_prompt(approval_id: str, action: str, description: str) -> None:
    """Envoie (ou renvoie, après une explication) le prompt Oui/Non/Explique-moi pourquoi."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from aria_core.gateway import telegram_bot

    if not telegram_bot.settings.admin_ids:
        raise RuntimeError("aucun admin_ids configuré — impossible de notifier")

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Oui", callback_data=f"approve:{approval_id}"),
                InlineKeyboardButton("❌ Non", callback_data=f"reject:{approval_id}"),
            ],
            [InlineKeyboardButton("❓ Explique-moi pourquoi", callback_data=f"explain:{approval_id}")],
        ]
    )

    text = (
        f"💸 Dépense ACP — validation requise #{approval_id}\n\n"
        f"Action : {action}\n"
        f"{description}\n\n"
        "Aucune dépense n'aura lieu sans ta validation explicite."
    )

    await telegram_bot.send_approval_keyboard(telegram_bot.settings.admin_ids[0], text, keyboard)


async def generate_spend_explanation(action: str, description: str, payload: dict[str, Any]) -> str:
    from aria_core.llm import chat_with_context

    system_context = (
        "Tu es ARIA. Explique en langage simple à ton administrateur pourquoi tu demandes "
        "cette dépense précise, avant qu'elle ne soit validée. Sois factuelle, concise "
        "(5-8 lignes), et rappelle que rien n'est encore exécuté tant qu'il n'a pas répondu."
    )
    user_message = (
        f"Action : {action}\n"
        f"Description : {description}\n"
        f"Détails : {json.dumps(payload, ensure_ascii=False)}\n\n"
        "Explique pourquoi cette dépense est demandée."
    )
    explanation = await chat_with_context(user_message, system_context, max_tokens=350)
    return explanation or (
        "Je n'ai pas pu générer d'explication automatique pour le moment — "
        "la demande reste en attente de ta décision (Oui/Non)."
    )


async def resolve_spend(approval_id: str, approved: bool, admin_id: str) -> str:
    """Exécute (ou refuse) une dépense après décision Telegram. Idempotent — un
    double-clic sur le même bouton ne peut pas déclencher une double exécution
    (transition atomique pending -> decision dans le ledger)."""
    # Kill-switch (fail-closed) : hard-stop argent. Même un clic « Oui » sur un vieux prompt ne
    # dépense pas tant qu'ARIA est en pause OU que l'état est illisible. L'entrée reste pending
    # (pas de claim) → réexécutable après /start. Un refus reste autorisé (aucun argent ne sort).
    if approved:
        _spend_block = outgoing_pause.money_block_reason(f"L'exécution de la dépense #{approval_id}")
        if _spend_block:
            return _spend_block
    decision = "approved" if approved else "rejected"
    entry = await claim_for_decision(approval_id, decision=decision, decided_by=admin_id)
    if entry is None:
        return f"Transaction #{approval_id} déjà traitée ou introuvable — aucune action supplémentaire."

    if not approved:
        await set_result(approval_id, "refusé par l'administrateur")
        append_memory(
            "wallet",
            f"[REFUSÉ] {entry['action']} {entry['amount']} / {entry['counterparty']} (#{approval_id})",
        )
        return f"❌ Dépense #{approval_id} refusée — aucune exécution."

    payload = json.loads(entry["payload"] or "{}")
    executor = WALLET_ACTIONS.get(entry["action"])
    if executor is None:
        result = f"aucun exécuteur enregistré pour {entry['action']}"
        await set_result(approval_id, result)
        return f"⚠️ Dépense #{approval_id} approuvée mais non exécutable : {result}"

    row, err = executor(payload)
    result_text = err if err else json.dumps(row or {}, ensure_ascii=False)
    await set_result(approval_id, result_text)

    if err:
        append_memory(
            "wallet",
            f"[ÉCHEC EXÉCUTION] {entry['action']} {entry['amount']} / {entry['counterparty']} "
            f"(#{approval_id}) : {err}",
        )
        return f"⚠️ Approuvé mais l'exécution a échoué — #{approval_id} : {err[:300]}"

    append_memory(
        "wallet",
        f"[EXÉCUTÉ] {entry['action']} {entry['amount']} / {entry['counterparty']} (#{approval_id})",
    )
    return f"✅ Dépense #{approval_id} exécutée : {entry['action']} {entry['amount']} / {entry['counterparty']}"
