"""Feu vert après dialogue Socrate — « ok vazy », « si bénéfique tu peux seulement »."""
from __future__ import annotations

import re
from typing import Any

from aria_core.grounding import is_short_ack

_DEFERRED_GO_RE = re.compile(
    r"(?:"
    r"^(?:ok[,.]?\s*)?(?:vazy|vas[- ]?y|go\b|allez[- ]?y|lance)\s*[!.]*$"
    r"|^ok\s+(?:vazy|vas[- ]?y|go)\b"
    r"|si c['']est b[eé]n[eé]fique pour toi seulement tu peux"
    r"|si [çc]a t['']aide.{0,30}tu peux"
    r"|tu peux(?:\s+seulement|\s+y aller)\s*[!.]*$"
    r"|benefique pour toi.{0,20}(?:fais|fait|tu peux)"
    r")",
    re.IGNORECASE,
)
_QUESTION_TURN_RE = re.compile(r"\?\s*$")


def wants_operator_deferred_go(message: str) -> bool:
    """Court feu vert après un fil de questions — pas « ok prevu »."""
    text = (message or "").strip()
    if not text or len(text) > 140:
        return False
    if is_short_ack(text):
        return False
    return bool(_DEFERRED_GO_RE.search(text))


def _substantive_user_turn(content: str) -> bool:
    t = (content or "").strip()
    if len(t) < 10:
        return False
    if wants_operator_deferred_go(t):
        return False
    if is_short_ack(t):
        return False
    return True


async def _recent_thread(*, limit: int = 10) -> list[dict[str, Any]]:
    try:
        from aria_core import repertoire_db

        return await repertoire_db.get_messages(limit=limit, visitor_id=None)
    except Exception:
        return []


def _thread_goal_hint(messages: list[dict[str, Any]]) -> str:
    """Derniers tours utilisateur substantiels + extrait dernière réponse ARIA."""
    users: list[str] = []
    last_agent = ""
    for msg in messages:
        role = msg.get("role") or ""
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user" and _substantive_user_turn(content):
            users.append(content)
        elif role == "agent":
            last_agent = content
    hint_parts: list[str] = []
    if users:
        hint_parts.append(users[-1][:300])
    if last_agent:
        clean = re.sub(r"\n\n[🟢🟠].*$", "", last_agent, flags=re.DOTALL)
        hint_parts.append(clean[:400])
    return " — ".join(hint_parts)[:500]


async def execute_deferred_go_ahead(
    message: str,
    *,
    lang: str = "fr",
) -> tuple[str, dict[str, Any]]:
    """
    Sylvain : questions → questions → ok vazy / si bénéfique tu peux.
    Agit depuis le fil récent sans nouveau gros appel LLM si possible.
    """
    lang_key = "fr" if lang == "fr" else "en"
    thread = await _recent_thread()
    goal_hint = _thread_goal_hint(thread)
    combined = f"{goal_hint}\n{message}".strip()

    data: dict[str, Any] = {
        "deferred_go": True,
        "goal_hint": goal_hint,
    }

    from aria_core.operator_self_directive import (
        OperatorMessageKind,
        classify_operator_message,
        parse_self_maintenance_action,
    )
    from aria_core.self_maintenance import execute_self_maintenance

    kind = classify_operator_message(combined)
    if kind in (OperatorMessageKind.SELF_DIRECTIVE, OperatorMessageKind.CURIOSITY_GAP):
        action = parse_self_maintenance_action(combined)
        if action:
            reply = await execute_self_maintenance(action, lang=lang_key)
            data["action"] = action.value
            return reply, data

    from aria_core.operator_readiness import (
        execute_operator_readiness,
        wants_operator_readiness,
    )

    synthetic = (
        f"ok tout est pret qu'est-ce qu'il manque pour que tu puisses {goal_hint}. "
        f"si c'est benefique pour toi fait le"
        if goal_hint
        else f"{message} si c'est benefique fait le"
    )
    if wants_operator_readiness(synthetic) or goal_hint:
        reply, rdata = await execute_operator_readiness(synthetic, lang=lang)
        data.update(rdata)
        data["deferred_go"] = True
        if lang_key == "fr":
            prefix = (
                "Feu vert reçu — je reprends notre échange et j'avance.\n\n"
                if goal_hint
                else "Feu vert reçu.\n\n"
            )
        else:
            prefix = "Go ahead — resuming our thread.\n\n"
        return prefix + reply, data

    if lang_key == "fr":
        reply = (
            "Feu vert noté — mais je ne vois pas d'action claire dans nos derniers échanges.\n"
            "Redis en une phrase l'objectif (ex. « déploie X », « file ouvrier pour Y »), "
            "ou continue le dialogue par questions."
        )
    else:
        reply = (
            "Go noted — no clear action in our recent thread.\n"
            "State the goal in one sentence, or keep asking questions."
        )
    return reply, data