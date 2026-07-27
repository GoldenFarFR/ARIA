"""Public ARIA mode — courtesy & verified information for visitors; operator tools gated."""

from __future__ import annotations

import hashlib
import os
import re
import secrets as _secrets

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
    # 18/07 -- found by security audit: absent from this blacklist, a
    # non-admin visitor could trigger execute_ingest_repo() (writes to
    # ARIA's cognitive/vector memory, add_knowledge(source="operator",
    # approved=True, ...)) via free text ("ingest repo", "feed my
    # memory"...) -- explicitly bypassed the spirit of the guardrail (its
    # own docstring says "never public").
    "ingest_repo",
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


def _admin_totp_secret() -> str:
    """Operator TOTP secret (opt-in) — lives in the VPS's .env, never in the repo."""
    return (os.environ.get("ADMIN_TOTP_SECRET") or "").strip()


# Anti-brute-force on the second factor: the TOTP code is only 6 digits (10^6). If the
# admin secret leaked, without a limit an attacker could try every code. We lock
# per IP beyond _TOTP_MAX_FAILS failures within _TOTP_WINDOW seconds (in-memory state, per
# process — a single container in prod). A success resets the IP's counter.
_TOTP_FAILS: dict[str, list[float]] = {}
_TOTP_MAX_FAILS = 8
_TOTP_WINDOW = 300.0


def _totp_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_operator_request(request: Request) -> bool:
    """True if the request carries a valid admin secret — WITHOUT raising an exception.

    Header only (`X-Admin-Secret`): we refuse the secret in the query string, which leaks
    into server logs, browser history, and the Referer header. Constant-time comparison
    so the secret isn't exposed to a timing attack.

    OPT-IN second factor (2FA): if `ADMIN_TOTP_SECRET` is set in the environment,
    a valid TOTP code (`X-Admin-Totp` header) is REQUIRED on top of the secret. Without this
    variable, behavior is unchanged (secret only) — no lock-out risk by default.
    """
    secret = (settings.admin_api_secret or "").strip()
    if not secret:
        return False
    provided = (request.headers.get("X-Admin-Secret") or "").strip()
    if not (provided and _secrets.compare_digest(provided, secret)):
        return False

    totp_secret = _admin_totp_secret()
    if totp_secret:
        import time

        from aria_core.admin_totp import verify_totp

        ip = _totp_client_ip(request)
        now = time.time()
        fails = [t for t in _TOTP_FAILS.get(ip, []) if now - t < _TOTP_WINDOW]
        if len(fails) >= _TOTP_MAX_FAILS:
            _TOTP_FAILS[ip] = fails  # anti-brute-force lock: stays blocked for the window's duration
            return False

        code = (request.headers.get("X-Admin-Totp") or "").strip()
        if not verify_totp(totp_secret, code):
            fails.append(now)
            _TOTP_FAILS[ip] = fails
            return False

        _TOTP_FAILS.pop(ip, None)  # success -> clear this IP's failures
    return True


def require_operator(request: Request) -> None:
    secret = (settings.admin_api_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=403, detail="Operator endpoints disabled.")
    if not is_operator_request(request):
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