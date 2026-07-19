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
    """Contexte factuel du pool/track-record pour ancrer l'initiative sur l'état RÉEL,
    jamais une ambition générique. Best-effort : une source indisponible n'empêche pas
    les autres (dégradation douce, jamais bloquant pour le heartbeat)."""
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
    except Exception as exc:  # noqa: BLE001 — best-effort, jamais bloquant
        logger.info("founder_ping: snapshot pool échoué (%s)", exc)

    try:
        from aria_core import vc_predictions, weekly_training

        total = await vc_predictions.total_predictions_count()
        due = await weekly_training.due_predictions_summary()
        # Distinction cruciale (trouvée le 14/07 via une initiative confabulée) :
        # "ouvert" (pas encore résolu) n'est PAS "à échéance" (horizon atteint,
        # prêt à clôturer avec un vrai résultat). Sans elle, le LLM proposait
        # de "finaliser" un pronostic qui ne pouvait objectivement rien produire.
        if due["due_now"] > 0:
            status = f"{due['due_now']} arrivé(s) à échéance -- prêt(s) à clôturer avec un résultat réel"
        elif due["open_total"] > 0:
            nearest = due["nearest_due_at"] or "date inconnue"
            status = f"{due['open_total']} ouvert(s) mais AUCUN à échéance (le plus proche le {nearest}) -- rien à finaliser maintenant"
        else:
            status = "aucun pronostic ouvert"
        lines.append(f"Track-record : {total} pronostic(s) au total, {status}.")
        # 19/07 -- même famille que la distinction due_now/open_total ci-dessus (2e
        # récurrence du même bug de fond, formulation différente : une initiative a
        # proposé un "verdict chiffré sur la fiabilité" de pronostics tous encore
        # ouverts). Rendre le fait explicite ICI, dans le contexte, pour que le LLM
        # n'ait jamais à le déduire lui-même.
        resolved = (await vc_predictions.metrics())["closed"]
        if resolved == 0:
            lines.append(
                "0 pronostic RÉSOLU à ce jour -- aucun taux de réussite/fiabilité "
                "réel n'est calculable, ne propose jamais un verdict chiffré là-dessus."
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("founder_ping: snapshot vc_predictions échoué (%s)", exc)

    return "\n".join(lines)


# 19/07 -- garde déterministe POST-génération (#141), suite au feedback opérateur
# direct sur une initiative confabulée ("si c'est des initiatives pourries autant
# qu'elle ne le dise pas"). C'est la 2e récurrence du même bug de fond sous une
# formulation différente (14/07 : proposait de "finaliser" un pronostic non échu ;
# 19/07 : proposait un "verdict chiffré sur la fiabilité" de pronostics tous non
# résolus, ET a relancé "l'initiative ACP marketplace" comme si elle était encore
# ouverte alors qu'ACP est abandonné par décision). Le contexte/prompt (ci-dessus)
# donne déjà les bons faits au LLM -- cette garde est le filet de sécurité si,
# malgré tout, il les ignore : dans ce cas, le message n'est JAMAIS envoyé (retourne
# None) plutôt que d'atterrir sur Telegram avec un défaut connu.
_RESOLUTION_DEPENDENT_CLAIM_RE = re.compile(
    r"fiabilit[ée]|taux de r[ée]ussite|win\s*rate|pr[ée]cision|reliability|accuracy|"
    r"finaliser[^.]{0,30}pronostic",
    re.IGNORECASE,
)
_ABANDONED_TOPIC_RE = re.compile(r"\bACP\b|marketplace|DEXPulse|Aria Market", re.IGNORECASE)


def _founder_ping_quality_violation(reply: str, *, resolved_count: int) -> str | None:
    """Retourne une courte raison si ``reply`` viole une garde connue, sinon None."""
    if resolved_count == 0 and _RESOLUTION_DEPENDENT_CLAIM_RE.search(reply):
        return "revendique une fiabilité/un taux de réussite alors qu'aucun pronostic n'est résolu"
    if _ABANDONED_TOPIC_RE.search(reply):
        return "mentionne un sujet déjà abandonné (ACP/marketplace/DEXPulse/Aria Market)"
    return None


def _last_initiative_recap() -> str:
    """Rappel de sa DERNIÈRE initiative (pas d'analyse automatisée de suivi -- juste le
    texte brut réinjecté) pour qu'elle se tienne responsable de ce qu'elle a annoncé au
    lieu d'enchaîner des idées déconnectées les unes des autres."""
    entries = read_recent_memory(category="proactive", limit=1)
    if not entries:
        return ""
    return entries[-1][:500]


async def run_founder_ping(lang: str = "fr") -> str | None:
    """One LLM-generated initiative for the operator (Telegram push).

    Ancrée sur l'état RÉEL du pool/track-record (`_real_state_snapshot`) et sur sa
    propre dernière initiative (`_last_initiative_recap`) -- avant ce correctif (10/07),
    c'était du texte LLM pur, sans lien avec les vraies données : une initiative pouvait
    promettre un pronostic sur "un token émergent" sans qu'aucun candidat réel n'existe
    au moment de la génération, et rien ne vérifiait si la promesse précédente avait
    été tenue.
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
    except Exception:  # noqa: BLE001 — fail-closed : compte inconnu = traité comme 0
        resolved_count = 0
    violation = _founder_ping_quality_violation(reply, resolved_count=resolved_count)
    if violation:
        logger.warning("founder_ping: initiative bloquée par le garde de qualité (%s)", violation)
        append_memory("proactive", f"[founder_ping][BLOQUÉ: {violation}] {reply[:300]}")
        return None

    append_memory("proactive", f"[founder_ping] {reply[:300]}")
    return reply