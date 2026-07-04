"""Réponses opérateur naturelles — style Grok/Cursor, pas épistémique ni murs de commandes."""
from __future__ import annotations

import re

from aria_core.capability_levels import CATEGORY_ORDER, check_auto_completions, full_status
from aria_core.runtime import settings

_COMPETENCE_IMPROVE_RE = re.compile(
    r"(?:"
    r"il te faut quoi|de quoi as[- ]?tu besoin|what do you need|"
    r"am[eé]liorer tes comp(?:[eé]tences?)?|improve your (?:skills|capabilities)|"
    r"renforcer tes comp|tes lacunes|tes faiblesses"
    r")",
    re.IGNORECASE,
)

_INJECTED_CLAIM_RE = re.compile(
    r"(?:"
    r"supprim[ée]|coup[ée]|retir[ée]|annonce|facture|facturation|passe[r]?\s+en|vient\s+de|désormais|"
    r"depuis\s+(?:hier|aujourd|ce\s+matin)|entre\s+hier|effective|impos[ée]|obligatoire|"
    r"augment|baisse|gagn[ée]|abonn[ée]s?|nouveaux?\s+abonn|dependabot|pr\s+merg|"
    r"gratuit\s+illimit|étoiles?|note\s+5|pourboire|uptime|contribut|"
    r"tweets?\s+automatiques|livré|livr[ée]|usdc|2fa|catalogue\s+spark|reste\s+dispo|"
    r"merg[ée]|déploy[ée]|commit\s+[a-f0-9]{6,}|class[ée]|"
    r"\d+\s*%|\d+[\s,.]?\d*\s*(?:\$|€|usd|usdc)|"
    r"le\s+\d{1,2}\s+(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)\s+\d{4}"
    r")",
    re.IGNORECASE,
)
_OPERATOR_COMMAND_RE = re.compile(
    r"(?:^/|crée|créer|creer|create\s+repo|level\s+up|montre\s+qi|check-aria|sync-render|"
    r"deploy|worker\s+delegate|/learn|/directive)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"(?:\?\s*$|^(?:est-ce|qu'?en\s+penses|tu\s+penses|comment|pourquoi|quoi|qui|quel|"
    r"as-tu|tu\s+as\s+prevu|tu\s+pref))",
    re.IGNORECASE,
)

_MORE_DETAIL_RE = re.compile(
    r"^(?:"
    r"arguments?\s+plus|plus\s+d['']?arguments?|d[eé]veloppe|en\s+d[eé]tail|"
    r"explique\s+plus|va\s+plus\s+loin|continue|pr[eé]cise"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)


def wants_capability_improvement(message: str) -> bool:
    return bool(_COMPETENCE_IMPROVE_RE.search((message or "").strip()))


def wants_more_detail_followup(message: str) -> bool:
    return bool(_MORE_DETAIL_RE.match((message or "").strip()))


def is_injected_factual_claim(message: str) -> bool:
    """Affirmation externe collée par l'opérateur — pas une question ni une commande."""
    text = (message or "").strip()
    if len(text) < 24:
        return False
    if _OPERATOR_COMMAND_RE.search(text):
        return False
    if _QUESTION_RE.search(text):
        return False
    if wants_capability_improvement(text):
        return False
    return bool(_INJECTED_CLAIM_RE.search(text))


def unverified_claim_reply(message: str, *, lang: str = "fr") -> str:
    snippet = (message or "").strip()[:120]
    if lang == "fr":
        return (
            f"Tu m'as collé une info externe (« {snippet}… ») — "
            "je n'ai rien dans JOURNAL, COLLEGUE ou GitHub qui la confirme. "
            "Je ne valide pas sans preuve. "
            "Si c'est un test d'hallucination : OK, je mord pas. "
            "Si tu veux que je creuse, dis « vérifie » ou donne une source."
        )
    return (
        f"You pasted an external claim (« {snippet}… ») — "
        "nothing in my operator memory or GitHub confirms it. "
        "I won't affirm without proof. Say « verify » if you want me to dig."
    )


def operator_improvement_reply(*, lang: str = "fr") -> str:
    """Ce dont ARIA a besoin pour monter en compétence — lecture locale QI."""
    check_auto_completions()
    status = full_status(lang)
    by_cat = status.get("categories") or {}
    ordered = sorted(
        CATEGORY_ORDER,
        key=lambda c: int((by_cat.get(c) or {}).get("level") or 0),
    )
    weak = ordered[:3]

    if lang == "fr":
        lines = [
            "Pour monter en compétence, il me faut surtout de l'exécution réelle, pas plus de théorie :",
        ]
        tips = {
            "codage": "plus de cycles ouvrier (PR mergées, tests verts) sur aria-core et aria-vanguard",
            "fiabilite": "moins d'incidents ops — health Render, secrets sync, runbook à jour",
            "autonomie": "boucles ACP/revenu et heartbeat qui tournent sans que tu relances",
            "business": "premiers jobs ACP payés livrés + log revenue_ledger",
            "intelligence": "mémoire ops (COLLEGUE, JOURNAL) tenue à jour multi-PC",
            "social": "X/Telegram réguliers sans promesses vides",
        }
        for cat in weak:
            lvl = int((by_cat.get(cat) or {}).get("level") or 0)
            hint = tips.get(cat, "pratique ciblée + validation opérateur")
            lines.append(f"• {cat} ({lvl}/1000) — {hint}")
        lines.append(
            f"\nIndice global : {status.get('global_index', '?')}/1000. "
            "Dis « montre qi » pour le tableau complet."
        )
        return "\n".join(lines)

    lines = ["To level up I need shipped work, not more theory:"]
    for cat in weak:
        lvl = int((by_cat.get(cat) or {}).get("level") or 0)
        lines.append(f"• {cat} ({lvl}/1000) — targeted practice + operator validation")
    lines.append(f"\nGlobal index: {status.get('global_index', '?')}/1000. Say « show qi » for full board.")
    return "\n".join(lines)


def llm_preference_reply(*, lang: str = "fr") -> str:
    provider = (settings.llm_provider or "none").strip().lower()
    model = (settings.llm_model or "").strip() or "défaut"
    if lang == "fr":
        return (
            "Pas de préférence « humaine » — j'utilise le bon moteur pour le job :\n"
            f"• **Spark (Virtuals)** — cerveau ARIA en prod ({provider} / {model}) — c'est ce qui tourne là.\n"
            "• **Groq** — secours rapide si Spark ou Virtuals flanche.\n"
            "• **Qwen local** — scout/KART sur ton PC, pas le bot Render.\n\n"
            "En clair : Spark pour converser avec toi, Qwen pour fouiller le repo en local, "
            "Groq en filet de sécurité."
        )
    return (
        "No human-style favorite — right engine for the job:\n"
        f"• Spark (Virtuals) — prod brain ({provider} / {model})\n"
        "• Groq — fast fallback\n"
        "• Qwen local — scout/KART on your PC\n"
    )