"""Public ARIA mode — courtesy & verified information for visitors; operator tools gated."""

from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException, Request

from aria_core.runtime import settings

VISITOR_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")

# Skills that modify code, internal state, or operator-only workflows — never public.
OPERATOR_SKILLS = frozenset({
    "github_sandbox",
    "build_optimize",
    "develop_repertoire",
    "manage_repertoire",
    "memory_recall",
    "marketing_comms",
    "analyze_portfolio",
    "zhc_bridge",
    "training_portfolio",
    "holding_site",
    "entrepreneur_cultivation",
    "acp_marketplace",
    "worker_delegate",
})


def is_public_mode() -> bool:
    return settings.aria_public_mode


def resolve_visitor_id(request: Request, body_visitor_id: str | None = None) -> str:
    header = (request.headers.get("X-Visitor-Id") or "").strip()
    candidate = header or (body_visitor_id or "").strip()
    if candidate and VISITOR_ID_RE.match(candidate):
        return candidate
    client = request.client.host if request.client else "unknown"
    ua = request.headers.get("User-Agent", "")
    digest = hashlib.sha256(f"{client}:{ua}".encode()).hexdigest()[:16]
    return f"anon-{digest}"


def require_operator(request: Request) -> None:
    secret = (settings.admin_api_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=403, detail="Operator endpoints disabled.")
    provided = (
        request.headers.get("X-Admin-Secret")
        or request.query_params.get("secret")
        or ""
    ).strip()
    if provided != secret:
        raise HTTPException(status_code=403, detail="Operator access required.")


def skill_allowed_in_public(skill_name: str | None) -> bool:
    if not skill_name:
        return True
    return skill_name not in OPERATOR_SKILLS


def operator_action_blocked_reply(lang: str = "en") -> str:
    if lang == "fr":
        return (
            "Cette action est réservée à l'opérateur — je ne modifie ni code, ni mémoire interne, "
            "ni configuration pour les visiteurs.\n\n"
            "En mode public : échanges courtois et informations vérifiées sur la holding, "
            "DEXPulse et le projet uniquement."
        )
    return (
        "That action is operator-only — I do not modify code, internal memory, or configuration "
        "for visitors.\n\n"
        "In public mode: courteous exchanges and verified information about the holding, "
        "DEXPulse, and the project only."
    )