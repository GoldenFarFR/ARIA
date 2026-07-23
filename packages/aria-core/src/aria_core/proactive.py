"""Proactive operator outreach — ARIA initiates Telegram when she has an initiative."""

from __future__ import annotations

import logging
import re

from aria_core.llm import chat_with_context, is_llm_configured
from aria_core.memory import append_memory, build_llm_context, read_recent_memory
from aria_core.narrative import llm_system_block
from aria_core.runtime import settings

logger = logging.getLogger(__name__)


def proactive_ideas_enabled() -> bool:
    return (
        settings.aria_proactive_ideas
        and is_llm_configured()
        and bool(settings.telegram_bot_token)
        and bool(settings.admin_ids)
    )


async def _real_state_snapshot() -> str:
    """Factual context on the pool/track-record to anchor the initiative on the REAL
    state, never a generic ambition. Best-effort: one unavailable source doesn't block
    the others (graceful degradation, never blocking for the heartbeat)."""
    lines: list[str] = []
    try:
        from aria_core.skills.candidate_ranking import top_candidates

        tops = await top_candidates(3)
        if tops:
            preview = "; ".join(
                f"{c.symbol or c.contract[:10]} (score {c.rank_score:.0f}, {c.verdict})"
                for c in tops
            )
            lines.append(f"Top candidats pool actuel : {preview}.")
        else:
            lines.append("Pool actif : aucun candidat disponible actuellement.")
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocking
        logger.info("founder_ping: snapshot pool échoué (%s)", exc)

    try:
        from aria_core import vc_predictions, weekly_training

        total = await vc_predictions.total_predictions_count()
        due = await weekly_training.due_predictions_summary()
        # Crucial distinction (found 14/07 via a confabulated initiative):
        # "open" (not yet resolved) is NOT "due" (horizon reached,
        # ready to close with a real result). Without it, the LLM would propose
        # to "finalize" a prediction that couldn't objectively produce anything.
        if due["due_now"] > 0:
            status = f"{due['due_now']} arrivé(s) à échéance -- prêt(s) à clôturer avec un résultat réel"
        elif due["open_total"] > 0:
            nearest = due["nearest_due_at"] or "date inconnue"
            status = f"{due['open_total']} ouvert(s) mais AUCUN à échéance (le plus proche le {nearest}) -- rien à finaliser maintenant"
        else:
            status = "aucun pronostic ouvert"
        lines.append(f"Track-record : {total} pronostic(s) au total, {status}.")
        # 19/07 -- same family as the due_now/open_total distinction above (2nd
        # recurrence of the same underlying bug, different wording: an initiative
        # proposed a "numeric reliability verdict" on predictions that were all still
        # open). Make the fact explicit HERE, in the context, so the LLM
        # never has to infer it itself.
        resolved = (await vc_predictions.metrics())["closed"]
        if resolved == 0:
            lines.append(
                "0 pronostic RÉSOLU à ce jour -- aucun taux de réussite/fiabilité "
                "réel n'est calculable, ne propose jamais un verdict chiffré là-dessus."
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("founder_ping: snapshot vc_predictions échoué (%s)", exc)

    return "\n".join(lines)


# 19/07 -- deterministic POST-generation guard (#141), following direct operator
# feedback on a confabulated initiative ("if these are lousy initiatives, at least
# don't say so"). This is the 2nd recurrence of the same underlying bug under
# different wording (14/07: proposed to "finalize" a prediction not yet due;
# 19/07: proposed a "numeric reliability verdict" on predictions that were all
# unresolved, AND revived the "ACP marketplace initiative" as if it were still
# open when ACP has been abandoned by decision). The context/prompt (above)
# already gives the LLM the right facts -- this guard is the safety net if,
# despite that, it ignores them: in that case the message is NEVER sent (returns
# None) instead of landing on Telegram with a known defect.
_RESOLUTION_DEPENDENT_CLAIM_RE = re.compile(
    r"fiabilit[ée]|taux de r[ée]ussite|win\s*rate|pr[ée]cision|reliability|accuracy|"
    r"finaliser[^.]{0,30}pronostic",
    re.IGNORECASE,
)
_ABANDONED_TOPIC_RE = re.compile(r"\bACP\b|marketplace|DEXPulse|Aria Market", re.IGNORECASE)


def _founder_ping_quality_violation(reply: str, *, resolved_count: int) -> str | None:
    """Returns a short reason if ``reply`` violates a known guard, otherwise None."""
    if resolved_count == 0 and _RESOLUTION_DEPENDENT_CLAIM_RE.search(reply):
        return "revendique une fiabilité/un taux de réussite alors qu'aucun pronostic n'est résolu"
    if _ABANDONED_TOPIC_RE.search(reply):
        return "mentionne un sujet déjà abandonné (ACP/marketplace/DEXPulse/Aria Market)"
    return None


def _last_initiative_recap() -> str:
    """Recap of her LAST initiative (no automated follow-up analysis -- just the
    raw text re-injected) so she holds herself accountable for what she announced
    instead of chaining disconnected ideas one after another."""
    entries = read_recent_memory(category="proactive", limit=1)
    if not entries:
        return ""
    return entries[-1][:500]


async def run_founder_ping(lang: str = "fr") -> str | None:
    """One LLM-generated initiative for the operator (Telegram push).

    Anchored on the REAL state of the pool/track-record (`_real_state_snapshot`) and on
    her own last initiative (`_last_initiative_recap`) -- before this fix (10/07), it
    was pure LLM text, with no link to real data: an initiative could promise a
    prediction on "an emerging token" with no real candidate existing at generation
    time, and nothing checked whether the previous promise had been kept.
    """
    if not proactive_ideas_enabled():
        return None

    lang_key = "fr" if lang.startswith("fr") else "en"
    context = await build_llm_context(public=False)
    state = await _real_state_snapshot()
    last_initiative = _last_initiative_recap()
    lang_hint = "Réponds en français." if lang_key == "fr" else "Reply in English."

    accountability_rule = ""
    if last_initiative:
        accountability_rule = (
            "\nTa DERNIÈRE initiative (ci-dessous) — commence par vérifier si tu l'as "
            "réellement tenue avant d'en proposer une nouvelle. Si non tenue, dis-le "
            "honnêtement en une phrase plutôt que d'enchaîner une promesse déconnectée. "
            "ATTENTION (19/07, incident réel) : si cette dernière initiative portait sur "
            "ACP/marketplace/Stripe/DEXPulse/Aria Market — ce sont des sujets DÉJÀ "
            "ABANDONNÉS par décision (pas un oubli à corriger, pas une promesse à "
            "tenir) — ne la traite JAMAIS comme un engagement encore ouvert, ignore-la "
            "et propose autre chose :\n"
            f"{last_initiative}\n"
        )

    system = (
        f"{context}\n\n"
        f"{llm_system_block(lang_key)}\n\n"
        "MISSION : tu INITIES la conversation (message spontané à l'opérateur).\n"
        "Priorité #1 : faire grandir le track-record VC/trading (vc_predictions) — aucun "
        "produit payant à vendre (ACP abandonné, Stripe retiré), pas de plan vague.\n"
        "Sinon : holding, site aria-vanguard, moteur d'analyse, sandbox, ou /learn.\n"
        "INTERDIT : promouvoir DEXPulse ou Aria Market comme produits live (tous deux retirés), "
        "ou promettre un produit/ACP/marketplace qui n'existe pas.\n"
        "Les 'candidats' du pool sont des CONTRATS TOKEN (adresses on-chain), jamais des "
        "personnes -- ne propose jamais de les 'contacter', 'interviewer' ou vérifier leur "
        "'disponibilité'. Une action concrète porte sur une analyse, un pronostic, un post, "
        "un commit -- jamais une interaction humaine avec un token.\n"
        "ÉTAT RÉEL ACTUEL (ancre-toi dessus, jamais un candidat/chiffre inventé) :\n"
        f"{state}\n"
        f"{accountability_rule}"
        "Propose UNE initiative concrète livrable <24h avec métrique (pronostic, tweet, PR, ledger) "
        "-- si tu proposes un pronostic, il doit porter sur un candidat RÉELLEMENT présent dans "
        "l'état ci-dessus, jamais un token générique inventé.\n"
        "RÈGLE ABSOLUE (#150, 13/07) : cette fonction ne fait qu'écrire ce message, elle n'exécute "
        "STRICTEMENT RIEN (aucun tweet, aucune publication, aucun commit ne part réellement d'ici). "
        "Ton texte doit donc TOUJOURS être une PROPOSITION à valider manuellement, jamais un fait "
        "accompli. INTERDIT : 'j'ai posté', 'je vais poster', 'je publie', 'c'est fait', ou toute "
        "formulation au passé/futur proche laissant croire qu'une action est déjà exécutée ou en "
        "cours. OBLIGATOIRE : formule au conditionnel ('je propose de...', 'à valider :', 'si tu es "
        "d'accord, je...') pour toute action mentionnée (X, TikTok, PR, publication).\n"
        "Format : verdict en 1 phrase, puis 2–4 lignes d'action proposée. "
        "Termine si pertinent par une ligne `/learn topic | leçon` prête à copier.\n"
        "Pas de salutation longue. Pas de faux succès technique. Max 180 mots.\n"
        f"{lang_hint}"
    )
    user = (
        "Génère ton message spontané d'initiative pour l'opérateur (comme si tu lui écrivais "
        "sans qu'il ait parlé)."
        if lang_key == "fr"
        else "Generate your spontaneous initiative message to the operator (as if unprompted)."
    )
    reply = await chat_with_context(
        user,
        system,
        temperature=max(settings.aria_llm_temperature, 0.35),
        max_tokens=400,
    )
    if not reply or not reply.strip():
        return None
    reply = reply.strip()

    try:
        from aria_core import vc_predictions

        resolved_count = (await vc_predictions.metrics())["closed"]
    except Exception:  # noqa: BLE001 — fail-closed: unknown count treated as 0
        resolved_count = 0
    violation = _founder_ping_quality_violation(reply, resolved_count=resolved_count)
    if violation:
        logger.warning("founder_ping: initiative bloquée par le garde de qualité (%s)", violation)
        append_memory("proactive", f"[founder_ping][BLOQUÉ: {violation}] {reply[:300]}")
        return None

    append_memory("proactive", f"[founder_ping] {reply[:300]}")
    return reply