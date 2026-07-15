"""Accès dédié à la lecture de diagnostics opérateur (pool de sourcing, futur log
agent-wallet — cf. CLAUDE.md 15/07, demande opérateur : pouvoir vérifier ces
diagnostics directement depuis une session Claude Code, y compris depuis le cloud
qui n'a pas d'accès VPS direct).

Accès dédié, minuscule : `ARIA_DIAGNOSTIC_TOKEN` (distinct du secret admin ET du
token relay `ARIA_RELAY_ACCESS_TOKEN`) — ne peut QUE lire ces diagnostics, rien
d'autre (pas de finance, pas de code, pas d'admin, pas le relais Claude/opérateur).
Fail-closed : sans ce token configuré, les endpoints de diagnostic renvoient 403
systématiquement. Même politique de comparaison à temps constant que
`relay_chat.verify_relay_access`/`public_mode.is_operator_request`.
"""
from __future__ import annotations

import os
import secrets as _secrets


def diagnostic_access_token() -> str:
    return (os.environ.get("ARIA_DIAGNOSTIC_TOKEN") or "").strip()


def diagnostics_enabled() -> bool:
    """Gate simple : sans token dédié configuré, les diagnostics sont inertes."""
    return bool(diagnostic_access_token())


def verify_diagnostic_access(provided: str | None) -> bool:
    configured = diagnostic_access_token()
    if not configured or not provided:
        return False
    return _secrets.compare_digest(provided.strip(), configured)
