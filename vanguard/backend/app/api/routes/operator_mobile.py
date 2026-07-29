"""Operator mobile routes (Item #201) -- Android fallback channel if Telegram goes
down: login, session check, chat, kill-switch status/version. Plan:
/root/.claude/plans/fizzy-plotting-map.md.

Dedicated to the mobile account (operator_account.py/operator_session.py) --
NEVER touches aria_core.public_mode.require_operator (the legacy server-to-server
X-Admin-Secret/X-Admin-Totp path), which stays the only auth for scripts. Two
auth paths cohabit, neither replaces the other.
"""
from __future__ import annotations

import asyncio
import os
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from aria_core.admin_totp import verify_totp
from aria_core.brain import aria_brain
from aria_core.outgoing_pause import pause_status
from aria_core.public_mode import is_operator_request

from app.auth import operator_account as accounts
from app.auth import operator_auth_log as auth_log
from app.auth import operator_session as sessions
from app.auth.rate_limit import check_rate_limit
from app.auth.visitor import client_ip

router = APIRouter(prefix="/aria/ops", tags=["operator-mobile"])

# Contract version for the mobile app -- an INTEGER, monotonic, explicitly
# INDEPENDENT of backend_version (different lifecycles: the backend can evolve
# without the mobile-facing contract changing at all).
MOBILE_API_VERSION = 1
MINIMUM_MOBILE_API_VERSION = 1

CHAT_RATE_LIMIT_PER_MINUTE = 30
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    totp_code: str = Field(..., min_length=6, max_length=6)
    installation_id: str | None = Field(default=None, max_length=128)
    device_label: str | None = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    token: str


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # Client-generated UUID, one per message (Phase 2 plan requirement) -- guards
    # against a client-side timeout + server-side success mismatch causing a
    # retried message to trigger AriaBrain's actions_taken twice.
    idempotency_key: str | None = Field(default=None, max_length=64)


# In-memory, short-TTL idempotency cache -- scoped to a single process/container,
# which is enough: the risk window this guards against is a client retry within
# seconds of a timeout, never a long-lived replay. Keyed by caller (session_id or
# legacy IP tag) + the client's own key, so one caller can never replay another's
# cached reply.
_IDEMPOTENCY_TTL_SECONDS = 120
_idempotency_cache: dict[str, tuple[float, dict]] = {}


def _idempotency_get(key: str) -> dict | None:
    entry = _idempotency_cache.get(key)
    if entry is None:
        return None
    expires_at, cached_response = entry
    if time.monotonic() > expires_at:
        _idempotency_cache.pop(key, None)
        return None
    return cached_response


def _idempotency_set(key: str, response: dict) -> None:
    now = time.monotonic()
    expired = [k for k, (expires_at, _) in _idempotency_cache.items() if expires_at < now]
    for k in expired:
        _idempotency_cache.pop(k, None)
    _idempotency_cache[key] = (now + _IDEMPOTENCY_TTL_SECONDS, response)


def _backend_version() -> str:
    # Same pattern already used by the health check (app/main.py) -- never a
    # second, divergent source of truth for the deployed commit.
    return (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown")[:12]


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _require_mobile_session(authorization: str | None, *, ip: str | None) -> dict:
    """Mobile-account session only -- used by /session, which needs the actual
    account identity (username/role), meaningless for the legacy secret+TOTP path."""
    token = _bearer_token(authorization)
    session = await sessions.verify_operator_session(token, ip=ip)
    if session is None:
        raise HTTPException(status_code=403, detail="Invalid or expired session")
    return session


async def require_operator_or_session(request: Request) -> dict:
    """Cohabitation, never a replacement (plan's own wording): the legacy
    server-to-server X-Admin-Secret/X-Admin-Totp path (is_operator_request,
    untouched) OR a mobile Bearer session -- either is enough for /chat, /status,
    and (Phase 3) /stop, /resume, /history. Returns a tagged dict so callers can
    tell which path authorized the request."""
    if is_operator_request(request):
        return {"mode": "legacy"}
    ip = client_ip(request)
    session = await _require_mobile_session(request.headers.get("Authorization"), ip=ip)
    return {"mode": "mobile", **session}


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    ip = client_ip(request)

    # Fixed-size throttle bucket, in addition to the per-account progressive
    # delay below -- a sliding window that ALWAYS releases on its own, never a
    # hard lockout (this is a fallback channel; disabling it is worse than a
    # slow attacker).
    allowed = check_rate_limit(
        f"operator_login:{body.username}",
        max_attempts=LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many attempts — slow down and retry shortly.")

    account = await accounts.get_account(body.username)
    if account is None:
        # Same failure shape as a wrong password -- never reveal whether the
        # username exists.
        await auth_log.record_event(event_type=auth_log.EVENT_LOGIN_FAILURE, username=body.username, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    delay = accounts.login_delay_seconds(account["failed_attempts"])
    if delay > 0:
        await asyncio.sleep(delay)

    password_ok = accounts.verify_password(account, body.password)
    totp_ok = verify_totp(account["totp_secret"], body.totp_code)

    if not password_ok or not totp_ok:
        await accounts.record_login_failure(account["id"])
        await auth_log.record_event(event_type=auth_log.EVENT_LOGIN_FAILURE, username=body.username, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await accounts.record_login_success(account["id"])
    await auth_log.record_event(
        event_type=auth_log.EVENT_LOGIN_SUCCESS, username=body.username, ip=ip,
        installation_id=body.installation_id,
    )

    token = await sessions.create_operator_session(
        account_id=account["id"],
        installation_id=body.installation_id,
        user_agent=request.headers.get("User-Agent"),
        ip=ip,
        device_label=body.device_label,
    )
    return LoginResponse(token=token)


@router.post("/logout")
async def logout(request: Request, authorization: str | None = Header(default=None)):
    """Idempotent: an invalid, already-revoked, or already-expired token all
    return 200 the same way -- the client never has to distinguish these cases."""
    ip = client_ip(request)
    token = _bearer_token(authorization)
    if token:
        revoked = await sessions.revoke_operator_session(token)
        if revoked:
            await auth_log.record_event(event_type=auth_log.EVENT_LOGOUT, ip=ip)
    return {"ok": True}


@router.get("/session")
async def session_status(request: Request, authorization: str | None = Header(default=None)):
    """Verifies (and slides forward, see operator_session.py) the session, and
    returns identity + version info in a single round trip at app launch."""
    ip = client_ip(request)
    session = await _require_mobile_session(authorization, ip=ip)
    account = await accounts.get_account_by_id(session["account_id"])
    if account is None:
        raise HTTPException(status_code=403, detail="Invalid or expired session")
    return {
        "username": account["username"],
        "role": account["role"],
        "expires_at": session["expires_at"],
        "backend_version": _backend_version(),
        "mobile_api": MOBILE_API_VERSION,
        "minimum_mobile_api": MINIMUM_MOBILE_API_VERSION,
    }


@router.get("/version")
async def version():
    return {
        "backend_version": _backend_version(),
        "mobile_api": MOBILE_API_VERSION,
        "minimum_mobile_api": MINIMUM_MOBILE_API_VERSION,
    }


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    """Dedicated route, distinct from /aria/chat -- never touches that route's
    own public rate-limit/scope filtering. Rate-limited per caller so a stolen
    token (or a leaked legacy secret) can't spam AriaBrain.process() for free."""
    auth = await require_operator_or_session(request)
    rate_key = auth.get("session_id") or f"legacy:{client_ip(request) or 'unknown'}"

    idempotency_cache_key = f"{rate_key}:{body.idempotency_key}" if body.idempotency_key else None
    if idempotency_cache_key:
        cached = _idempotency_get(idempotency_cache_key)
        if cached is not None:
            return cached

    allowed = check_rate_limit(
        f"operator_chat:{rate_key}",
        max_attempts=CHAT_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many messages — slow down.")

    result = await aria_brain.process(
        body.message.strip(),
        visitor_id="operator-mobile",
        public_mode=False,
    )
    result_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)

    if idempotency_cache_key:
        _idempotency_set(idempotency_cache_key, result_dict)
    return result_dict


@router.get("/status")
async def status(request: Request):
    await require_operator_or_session(request)
    st = pause_status()
    return {
        "paused": st["paused"],
        "since": st["since"].isoformat() if st["since"] else None,
        "by": st["by"],
        "reason": st["reason"],
        "readable": st["readable"],
    }
