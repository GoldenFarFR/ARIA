"""Dedicated access for reading operator diagnostics (sourcing pool, future
agent-wallet log — cf. CLAUDE.md 15/07, operator request: be able to check
these diagnostics directly from a Claude Code session, including from the cloud
which doesn't have direct VPS access).

Tiny, dedicated access: `ARIA_DIAGNOSTIC_TOKEN` (distinct from the admin secret AND
the relay token `ARIA_RELAY_ACCESS_TOKEN`) — can ONLY read these diagnostics, nothing
else (no finance, no code, no admin, no Claude/operator relay).
Fail-closed: without this token configured, diagnostic endpoints systematically
return 403. Same constant-time comparison policy as
`relay_chat.verify_relay_access`/`public_mode.is_operator_request`.
"""
from __future__ import annotations

import os
import secrets as _secrets


def diagnostic_access_token() -> str:
    return (os.environ.get("ARIA_DIAGNOSTIC_TOKEN") or "").strip()


def diagnostics_enabled() -> bool:
    """Simple gate: without a dedicated token configured, diagnostics are inert."""
    return bool(diagnostic_access_token())


def verify_diagnostic_access(provided: str | None) -> bool:
    configured = diagnostic_access_token()
    if not configured or not provided:
        return False
    return _secrets.compare_digest(provided.strip(), configured)
