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
    """Secret TOTP opérateur (opt-in) — vit dans le .env du VPS, jamais dans le repo."""
    return (os.environ.get("ADMIN_TOTP_SECRET") or "").strip()


# Anti-force-brute du second facteur : le code TOTP ne fait que 6 chiffres (10^6). Si le
# secret admin fuitait, sans limite un attaquant pourrait tenter tous les codes. On verrouille
# par IP au-delà de _TOTP_MAX_FAILS échecs dans _TOTP_WINDOW secondes (état en mémoire, par
# processus — un seul conteneur en prod). Un succès réinitialise le compteur de l'IP.
_TOTP_FAILS: dict[str, list[float]] = {}
_TOTP_MAX_FAILS = 8
_TOTP_WINDOW = 300.0


def _totp_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_operator_request(request: Request) -> bool:
    """Vrai si la requête porte le secret admin valide — SANS lever d'exception.

    Header seul (`X-Admin-Secret`) : on refuse le secret en query-string, qui fuit
    dans les logs serveur, l'historique navigateur et l'en-tête Referer. Comparaison
    à temps constant pour ne pas exposer le secret à une attaque temporelle.

    Second facteur (2FA) OPT-IN : si `ADMIN_TOTP_SECRET` est défini dans l'environnement,
    un code TOTP valide (header `X-Admin-Totp`) est EXIGÉ en plus du secret. Sans cette
    variable, comportement inchangé (secret seul) — aucun risque de lock-out par défaut.
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
            _TOTP_FAILS[ip] = fails  # verrou anti-force-brute : reste bloqué le temps de la fenêtre
            return False

        code = (request.headers.get("X-Admin-Totp") or "").strip()
        if not verify_totp(totp_secret, code):
            fails.append(now)
            _TOTP_FAILS[ip] = fails
            return False

        _TOTP_FAILS.pop(ip, None)  # succès -> on efface les échecs de cette IP
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