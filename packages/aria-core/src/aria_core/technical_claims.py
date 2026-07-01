"""Detect LLM replies that fake GitHub/deploy success without skill proof."""
from __future__ import annotations

import re

_FAKE_SUCCESS = re.compile(
    r"(?:"
    r"commit\s+(?:github\s+)?(?:créé|created|poussé|pushed)|"
    r"(?:feat|fix)\([^)]+\).*(?:commit|poussé|pushed)|"
    r"déploiement\s+en\s+cours|deployment\s+in\s+progress|"
    r"site\s+déployé|site\s+deployed|"
    r"audit\s+terminé.*mvp|mvp\s+public\s+pages\s+créées|"
    r"tout\s+semble\s+fonctionner|everything\s+(?:seems\s+)?(?:to\s+)?work"
    r")",
    re.I | re.DOTALL,
)


def claims_unverified_github_success(text: str) -> bool:
    if not text or len(text) < 40:
        return False
    return bool(_FAKE_SUCCESS.search(text))


def _skill_confirms_github(skill: str | None, data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("committed") or data.get("deleted") or data.get("github_commit_sha"):
        return True
    if data.get("exists") is not None and skill == "github_sandbox":
        return True
    if data.get("status") == "ok" and skill == "github_sandbox":
        return True
    if data.get("audit_complete") and (data.get("committed") or data.get("write_denied")):
        return True
    if data.get("patch_complete") and (
        data.get("committed") or data.get("write_denied") or data.get("already_present")
    ):
        return True
    return False


def reject_fake_technical_success(
    reply: str,
    lang: str,
    *,
    skill_used: str | None,
    data: dict | None,
) -> str:
    """Replace hallucinated build/deploy claims when no skill confirmed the action."""
    payload = data if isinstance(data, dict) else {}
    skill_key = skill_used.value if hasattr(skill_used, "value") else skill_used
    if _skill_confirms_github(skill_key, payload):
        return reply
    if not claims_unverified_github_success(reply):
        return reply
    if lang == "fr" or (lang or "").startswith("fr"):
        return (
            "Je n'ai pas exécuté d'action GitHub ni de déploiement pour ce message — "
            "la réponse précédente était une projection LLM, pas un livrable réel.\n\n"
            "Pour lancer le site holding de façon vérifiable, envoie :\n"
            "« lancer le site holding » (audit GitHub + journal) ou « github status ».\n\n"
            "Pour modifier aria-vanguard : Cursor sur ce PC, ou droits d'écriture "
            "GITHUB_WRITE_REPOS sur le repo."
        )
    return (
        "I did not run a verified GitHub or deploy action for that message — "
        "the prior reply was LLM projection, not a real deliverable.\n\n"
        "To run a verifiable holding-site step, send:\n"
        "« lancer le site holding » (GitHub audit + journal) or « github status ».\n\n"
        "To edit aria-vanguard: use Cursor locally, or grant GITHUB_WRITE_REPOS on that repo."
    )