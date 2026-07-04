"""Gate anti-hallucination — règles + passage Groq léger avant envoi."""

from __future__ import annotations

import re

_INVENTED_METRICS = re.compile(
    r"(?:nous? (sommes?|avons?|générons?|gagnons?)|revenue (is|at|currently|maintenant|est)|chiffre d'affaires (est|de)|nous sommes à|revenue :|mrr :|arr :).{0,40}"
    r"(revenu|revenue|profit|mrr|arr|croissance|growth).{0,20}"
    r"(\$|usd|€|eur|\d+\s*%|\d+k|\d+\s*m)",
    re.I,
)
_FAKE_DEPLOY = re.compile(
    r"(déployé|deployed|pushé|pushed|commité|committed).{0,40}"
    r"(sans|without|aucun|no).{0,20}(url|lien|link|github)",
    re.I,
)
_GITHUB_CLAIM = re.compile(
    r"(repo créé|repository created|j'ai créé le repo|i created the repo|"
    r"le code est en prod|code is live)",
    re.I,
)

_CRITIC_PROMPT_FR = """Tu es un filtre anti-hallucination pour ARIA ZHC.

Réponse candidate :
{reply}

Réponds UNE ligne exactement :
SAFE: OUI ou NON — la réponse affirme-t-elle des métriques/revenus/chiffres/déploys non prouvés comme faits actuels ? (ignore les discussions hypothétiques ou citations de l'utilisateur)"""


async def critic_check(
    reply: str,
    lang: str = "fr",
    *,
    skill_used: str | None = None,
    data: dict | None = None,
) -> tuple[bool, str, dict]:
    """
    Return (safe, adjusted_reply, meta).
    safe=False → reply gets uncertainty disclaimer prepended.
    """
    meta: dict = {"critic": "pass"}
    if not reply or len(reply) < 20:
        return True, reply, meta

    data = data or {}
    if data.get("epistemic_static") or data.get("faq_direct"):
        return True, reply, meta
    if skill_used in ("faq_content", "epistemic_check", "memory_recall"):
        if data.get("groq_calibrated") or data.get("epistemic_static"):
            pass

    issues: list[str] = []
    if _INVENTED_METRICS.search(reply) and not data.get("revenue_logged"):
        if "logué" not in reply.lower() and "logged" not in reply.lower():
            issues.append("invented_metrics")
    if _GITHUB_CLAIM.search(reply) and not data.get("github_url") and "http" not in reply:
        issues.append("fake_github")
    if _FAKE_DEPLOY.search(reply):
        issues.append("fake_deploy")

    if not issues:
        return True, reply, meta

    from aria_core.runtime import settings

    if getattr(settings, "aria_epistemic_critic", True) and issues:
        try:
            from aria_core.llm import chat_with_context, is_llm_configured

            if is_llm_configured():
                raw = await chat_with_context(
                    reply[:600],
                    _CRITIC_PROMPT_FR.format(reply=reply[:500]),
                    temperature=0.0,
                    max_tokens=30,
                )
                if raw and "SAFE: NON" in raw.upper():
                    issues.append("groq_critic")
        except Exception:
            pass

    if issues:
        disclaimer = (
            "⚠️ Non vérifié — je n'ai pas de preuve documentée pour une partie de cette réponse.\n\n"
            if lang == "fr"
            else "⚠️ Unverified — I lack documented proof for part of this answer.\n\n"
        )
        meta = {"critic": "flagged", "issues": issues}
        return False, disclaimer + reply, meta

    return True, reply, meta