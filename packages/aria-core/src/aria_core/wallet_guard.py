"""ACP spend guard-rail -- mandatory Telegram escalation before any financial execution.

Structurally separate from ``telegram_bot.request_approval``: this module NEVER
references ``settings.aria_autonomous`` -- spends stay guarded regardless of the general
autonomy mode. ``escalate_spend`` only creates the records and notifies Telegram; it
never calls ``acp_cli``. The real execution lives exclusively in ``resolve_spend``,
reachable only from a real Telegram click (``telegram_bot._handle_callback``). If the
escalation cannot be delivered, the action stays blocked as pending -- no spend happens.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from aria_core import custody_pause, outgoing_pause
from aria_core.approvals import create_approval
from aria_core.memory import append_memory
from aria_core.skills.acp_cli import client_fund_job
from aria_core.wallet_ledger import claim_for_decision, create_ledger_entry, set_result

logger = logging.getLogger(__name__)


def _exec_client_fund_job(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    return client_fund_job(
        payload["job_id"],
        amount_usdc=payload.get("amount_usdc"),
        chain_id=payload.get("chain_id", "8453"),
    )


def _exec_trade_tokens(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    """Executes an ACP trade -- FAIL-CLOSED on slippage (absolute rule 09/07).

    07/08 -- real gap found by the #259 proactive audit, verified end to end
    before fixing: no caller has ever put a ``slippage`` key in this payload
    (`acp_client_actions.py` builds token_in/token_out/amount_in only), so
    ``payload.get("slippage", "")`` was always empty, so `acp_cli.trade_tokens`
    skipped ``--slippage`` entirely (`if slippage.strip():`), so acp-cli applied
    ITS OWN DEFAULT. That is exactly the founding 09/07 incident (ETH->USDC
    swap defaulting to 30%, docs/HANDOFF_SECURITE.md) which produced the
    absolute rule: "slippage never above 10%, always explicit, never a trade
    tool's default value".

    Why this BLOCKS instead of forcing a value: the unit acp-cli expects for
    ``--slippage`` is unverified -- the flag has never been passed, no test or
    doc in this repo records it, and acp-cli is a Windows-side npm package
    (absent from the VPS container: `which acp` fails, APPDATA empty, so
    `run_acp` already exits 127 here). Guessing "10" when the tool wants a
    fraction would mean 1000% slippage -- strictly worse than the bug being
    fixed. Project doctrine on an unverifiable value is fail-closed and say
    why, never invent precision (cf. CLAUDE.md "Robustness / degradation").

    To re-open this path: verify acp-cli's real ``--slippage`` unit (percent
    vs fraction vs bps) against the installed package, then call
    ``acp_cli.trade_tokens(..., slippage=<explicit validated value>)`` here --
    passing it unconditionally, never via a ``.get(..., "")`` that silently
    degrades to the tool's default again. Deliberately does NOT import
    `agent_wallet_pilot`'s MAX_SLIPPAGE_BPS -- that pilot is structurally
    separate from this shared guardrail (locked by test_coherence); both
    modules answer to the same CLAUDE.md rule, never to each other."""
    return None, (
        "Trade ACP bloqué (fail-closed) : le slippage ne peut pas être garanti <=10%. "
        "Aucun appelant ne fournit de slippage explicite, et l'unité attendue par "
        "acp-cli (--slippage) n'est pas vérifiée -- exécuter reviendrait à accepter "
        "la valeur par défaut de l'outil, ce qu'interdit la règle absolue du 09/07. "
        "Vérifier l'unité réelle d'acp-cli avant de réactiver ce chemin."
    )


def _exec_onchain_anchor_sepolia(payload: dict[str, Any]) -> tuple[dict | None, str | None]:
    from aria_core.onchain.sepolia_wallet import send_anchor_transaction

    try:
        tx_hash = send_anchor_transaction(
            contract=payload["contract"],
            root=payload["root"],
            chain_id=payload["chain_id"],
        )
    except Exception as exc:  # noqa: BLE001 -- a failed transaction must propagate, not crash
        return None, str(exc)
    return {"tx_hash": tx_hash}, None


WALLET_ACTIONS: dict[str, Callable[[dict[str, Any]], tuple[dict | None, str | None]]] = {
    "client_fund_job": _exec_client_fund_job,
    "trade_tokens": _exec_trade_tokens,
    "onchain_anchor_sepolia": _exec_onchain_anchor_sepolia,
}


class SpendEscalationError(RuntimeError):
    """Raised when the Telegram escalation could not be delivered -- no spend happens."""


async def escalate_spend(
    action: str,
    *,
    amount: str,
    counterparty: str,
    description: str,
    payload: dict[str, Any],
) -> str:
    """Creates the approval + the ledger and sends the 3-option Telegram prompt.

    Never calls the ACP executor -- execution only happens in ``resolve_spend``,
    triggered solely by a real Telegram click. If the send fails, the entry stays
    ``pending`` indefinitely: no spend happens.
    """
    # Kill-switch (fail-closed for money): paused OR if the state is unreadable/corrupted,
    # we don't even create the escalation. Callers (acp_client_actions, _handle_test_spend)
    # already catch SpendEscalationError and display the message.
    # Item #62 (08/03): checks BOTH the manual /stop flag (outgoing_pause)
    # and the dedicated custody auto-arm flag (custody_pause) -- either one
    # blocks real spending, but only outgoing_pause is shared with paper
    # trading now.
    _spend_block = outgoing_pause.money_block_reason("Cette dépense") or custody_pause.money_block_reason(
        "Cette dépense"
    )
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
    """Sends (or resends, after an explanation) the Yes/No/Explain-why prompt."""
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
    from aria_core.llm_economy import LlmDepth, anthropic_depth_override

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
    provider, model = anthropic_depth_override(LlmDepth.STANDARD)
    explanation = await chat_with_context(
        user_message, system_context, max_tokens=350, provider=provider, model=model
    )
    return explanation or (
        "Je n'ai pas pu générer d'explication automatique pour le moment — "
        "la demande reste en attente de ta décision (Oui/Non)."
    )


async def resolve_spend(approval_id: str, approved: bool, admin_id: str) -> str:
    """Executes (or refuses) a spend after a Telegram decision. Idempotent -- a
    double-click on the same button cannot trigger a double execution (atomic
    pending -> decision transition in the ledger)."""
    # Kill-switch (fail-closed): money hard-stop. Even a "Yes" click on an old prompt does not
    # spend while ARIA is paused OR the state is unreadable. The entry stays pending (no claim)
    # -> re-executable after /start. A refusal stays allowed (no money leaves).
    if approved:
        _spend_block = outgoing_pause.money_block_reason(
            f"L'exécution de la dépense #{approval_id}"
        ) or custody_pause.money_block_reason(f"L'exécution de la dépense #{approval_id}")
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
