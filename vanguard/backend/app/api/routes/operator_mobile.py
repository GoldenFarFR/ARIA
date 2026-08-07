"""Operator mobile routes (Item #201) -- Android fallback channel if Telegram goes
down: login, session check, chat, kill-switch status/arm/lift/history/version.
Plan: /root/.claude/plans/fizzy-plotting-map.md.

Dedicated to the mobile account (operator_account.py/operator_session.py) --
NEVER touches aria_core.public_mode.require_operator (the legacy server-to-server
X-Admin-Secret/X-Admin-Totp path), which stays the only auth for scripts. Two
auth paths cohabit, neither replaces the other.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from aria_core import kill_incident_log
from aria_core.admin_totp import verify_totp
from aria_core.brain import aria_brain
from aria_core.locale import LANG_FR
from aria_core.models import ChatResponse
from aria_core.outgoing_pause import pause, pause_status, resume
from aria_core.public_mode import is_operator_request

from app.auth import operator_account as accounts
from app.auth import operator_auth_log as auth_log
from app.auth import operator_session as sessions
from app.auth import operator_totp_replay as totp_replay
from app.auth.rate_limit import check_rate_limit
from app.auth.visitor import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/aria/ops", tags=["operator-mobile"])

# Contract version for the mobile app -- an INTEGER, monotonic, explicitly
# INDEPENDENT of backend_version (different lifecycles: the backend can evolve
# without the mobile-facing contract changing at all).
MOBILE_API_VERSION = 1
MINIMUM_MOBILE_API_VERSION = 1

CHAT_RATE_LIMIT_PER_MINUTE = 30
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900

# Kill-switch second-factor throttle. Budget is consumed by EVERY attempt, not
# only failures: a counter incremented after the check would still let a lucky
# guess through, which is the whole thing this bounds (verify_totp accepts 3 of
# 10^6 codes per attempt with its ±1-step tolerance). Deliberately generous --
# a real operator with an authenticator app needs one or two tries, and the plan
# forbids anything that could hard-lock a FALLBACK channel: this window always
# releases on its own, and Telegram /stop stays available regardless.
KILL_SWITCH_MAX_ATTEMPTS = 15
KILL_SWITCH_WINDOW_SECONDS = 300

# The client asks, the server caps -- never an unbounded dump of the incident log.
HISTORY_DEFAULT_LIMIT = 25
HISTORY_MAX_LIMIT = 100


class LoginRequest(BaseModel):
    # 07/08 -- TOTP dropped from THIS login form (operator request, "je veut
    # aussi que tu désactive le totp"): the app's own biometric lock
    # (biometricLock.ts) already gates every re-entry after the first login,
    # and the session is now effectively permanent (SESSION_TTL, see
    # operator_session.py), so this form is only ever seen once. Deliberately
    # UNRELATED to the kill-switch TOTP (_require_fresh_totp below, guarding
    # /stop and /resume specifically) -- that one stays required on every
    # single call, never weakened by this change.
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    installation_id: str | None = Field(default=None, max_length=128)
    device_label: str | None = Field(default=None, max_length=64)


class LoginResponse(BaseModel):
    token: str


class KillSwitchBody(BaseModel):
    # Bounded, but NOT length-validated to exactly 6 digits on purpose: verify_totp
    # already rejects a malformed code without raising, so every bad code -- wrong,
    # malformed, or missing -- comes back as the same 403 instead of a 422 that
    # would tell an attacker which kind of input they got wrong.
    totp_code: str | None = Field(default=None, max_length=16)
    reason: str | None = Field(default=None, max_length=280)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    # Client-generated UUID, one per message (Phase 2 plan requirement) -- guards
    # against a client-side timeout + server-side success mismatch causing a
    # retried message to trigger AriaBrain's actions_taken twice.
    idempotency_key: str | None = Field(default=None, max_length=64)
    # French default, DELIBERATELY diverging from AriaBrain.process()'s own
    # LANG_EN default -- do not "fix" this back to match it. This channel has a
    # single, non-English-speaking user (CLAUDE.md, operator profile): on Telegram
    # his client auto-translates the display, which hid the problem, but the
    # Android app translates nothing. A fallback channel meant for an emergency
    # answering in a language its only user cannot read is a functional failure.
    # Optional so the already-installed APK gets French without a new build,
    # while a future client can still choose.
    lang: Literal["fr", "en"] = LANG_FR


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


# Same message for a wrong code and for a replayed one: an attacker replaying a
# captured code must not learn that the code itself was valid. Still actionable
# for the operator, who only ever needs to know to wait for the next code.
_TOTP_REQUIRED_DETAIL = (
    "A fresh TOTP code is required — each code works only once, wait for the next one."
)


async def _require_fresh_totp(auth: dict, code: str | None) -> None:
    """Mandatory second factor on EVERY /stop and /resume call, never cached and
    never derived from the session: the sliding session is fine for chat but far
    too permissive for the only button that can really hurt (plan's own wording).

    The legacy server-to-server caller is exempt -- it already presented its own
    X-Admin-Totp (a DIFFERENT secret, ADMIN_TOTP_SECRET) to is_operator_request,
    which has its own brute-force lock. Assumed difference in level, documented in
    the plan: that path serves scripts, not a human app reusing one code twice.
    """
    if auth.get("mode") == "legacy":
        return

    account_id = auth.get("account_id")
    account = await accounts.get_account_by_id(account_id) if account_id is not None else None
    if account is None:
        raise HTTPException(status_code=403, detail="Invalid or expired session")

    if not check_rate_limit(
        f"operator_killswitch:{account_id}",
        max_attempts=KILL_SWITCH_MAX_ATTEMPTS,
        window_seconds=KILL_SWITCH_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Too many attempts — retry shortly.")

    code = (code or "").strip()
    # Verify BEFORE claiming: an invalid code must never leave a replay row behind.
    if not verify_totp(account["totp_secret"], code):
        raise HTTPException(status_code=403, detail=_TOTP_REQUIRED_DETAIL)
    if not await totp_replay.claim_code(account_id=account_id, totp_code=code):
        raise HTTPException(status_code=403, detail=_TOTP_REQUIRED_DETAIL)


def _pause_payload(st: dict) -> dict:
    """Single shape for /status, /stop and /resume, so the app can render the
    outcome of an action and the result of a later /status refresh identically."""
    return {
        "paused": st["paused"],
        "since": st["since"].isoformat() if st["since"] else None,
        "by": st["by"],
        "reason": st["reason"],
        "readable": st["readable"],
    }


async def _record_incident_best_effort(*, event_type: str, by: str, reason: str) -> None:
    """Same guard as telegram_bot._record_kill_incident: record_incident already
    swallows its own DB failures, but an unexpected error must not turn a stop
    that ALREADY happened into a 500 the app would read as "it failed". The
    action is done by the time we get here; audit is observability, never a gate.
    """
    try:
        await kill_incident_log.record_incident(
            event_type=event_type,
            trigger_source=kill_incident_log.TRIGGER_MANUAL,
            by=by,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 -- audit logging must never block the kill-switch
        logger.warning("operator_mobile: kill incident logging failed", exc_info=True)


def _actor(auth: dict) -> str:
    """Who armed/lifted, for pause_state + kill_incident_log. Uses the account's
    internal id, never its username (the plan keeps the username renameable), and
    stays distinguishable from a Telegram numeric id so the incident log shows
    which channel acted."""
    if auth.get("mode") == "legacy":
        return "ops-api:legacy"
    return f"mobile:{auth.get('account_id')}"


# Real incident (30/07): the operator typed "/stop" as a plain chat message and
# ARIA answered "Stop confirmed" -- a pure LLM confabulation, since the free-text
# chat only ever calls aria_brain.process(), never outgoing_pause.pause(). Proven
# live: a routine limit-order alert kept arriving right after the fake "stop".
# Telegram never has this problem because /stop there is a dedicated
# CommandHandler intercepted BEFORE the brain (see gateway/telegram_bot.py's
# _handle_stop) -- this mirrors that same interception for this channel, until
# the app gets its own real STOP button calling POST /stop directly (tracked
# separately, lower priority). Matched on the FIRST WORD only, so a genuine
# question that happens to contain "stop" (e.g. "why no stop loss on this
# token?") is never caught by mistake.
_CONTROL_COMMANDS = {"/stop", "/resume", "/pause", "/start"}

_CONTROL_COMMAND_REPLY = {
    "fr": (
        "Ce chat ne peut pas encore armer ou lever le kill-switch — un message ici "
        "n'est qu'une conversation avec ARIA, jamais une action réelle. Utilise "
        "Telegram (/stop, /resume) pour l'instant ; un vrai bouton dans cette app "
        "arrivera dans une prochaine mise à jour."
    ),
    "en": (
        "This chat cannot yet arm or lift the kill-switch — a message here is only "
        "ever a conversation with ARIA, never a real action. Use Telegram (/stop, "
        "/resume) for now; a real button in this app is coming in a later update."
    ),
}


def _control_command_reply(message: str, lang: str) -> ChatResponse | None:
    first_word = message.strip().split(maxsplit=1)[0].lower() if message.strip() else ""
    if first_word not in _CONTROL_COMMANDS:
        return None
    return ChatResponse(reply=_CONTROL_COMMAND_REPLY.get(lang, _CONTROL_COMMAND_REPLY["en"]))


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

    if not password_ok:
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

    # Checked BEFORE the brain, never after: a fixed reply here, the brain never
    # invoked at all -- see _control_command_reply's own docstring for why.
    control_reply = _control_command_reply(body.message, body.lang)
    if control_reply is not None:
        result_dict = control_reply.model_dump()
        if idempotency_cache_key:
            _idempotency_set(idempotency_cache_key, result_dict)
        return result_dict

    result = await aria_brain.process(
        body.message.strip(),
        lang=body.lang,
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
    return _pause_payload(pause_status())


@router.post("/stop")
async def stop(request: Request, body: KillSwitchBody | None = None):
    """Arms the kill-switch. Session AND a fresh TOTP code, both required.

    Idempotent: arming an already-armed pause returns 200 with `changed=false`,
    never an error -- a network retry must never look like a failure on the one
    control the operator reaches for in a hurry. No incident is recorded when
    nothing actually changed, so retries don't pollute the audit history.
    """
    auth = await require_operator_or_session(request)
    body = body or KillSwitchBody()
    await _require_fresh_totp(auth, body.totp_code)

    before = pause_status()
    if before["paused"]:
        return {"changed": False, **_pause_payload(before)}

    actor = _actor(auth)
    reason = (body.reason or "").strip() or "Manual stop (operator mobile channel)"
    # Real action first, audit log second: record_incident is best-effort by
    # construction (it swallows its own failures) and must never sit between the
    # operator and the pause actually being armed.
    after = pause(by=actor, reason=reason)
    await _record_incident_best_effort(
        event_type=kill_incident_log.EVENT_ARMED, by=actor, reason=reason,
    )
    return {"changed": True, **_pause_payload(after)}


@router.post("/resume")
async def resume_route(request: Request, body: KillSwitchBody | None = None):
    """Lifts the kill-switch. Same two factors as /stop, same idempotence.

    Also acts on an UNREADABLE pause state, not only on an armed one: a corrupted
    pause_state.json reads as `paused=False` but freezes spending fail-closed
    (outgoing_pause.money_block_reason), so treating it as "already resumed"
    would leave the operator with no way to unfreeze the money from this channel.
    Writing a clean state repairs it, and that IS a real state change -- logged.
    """
    auth = await require_operator_or_session(request)
    body = body or KillSwitchBody()
    await _require_fresh_totp(auth, body.totp_code)

    before = pause_status()
    if not before["paused"] and before["readable"]:
        return {"changed": False, **_pause_payload(before)}

    actor = _actor(auth)
    after = resume(by=actor)
    await _record_incident_best_effort(
        event_type=kill_incident_log.EVENT_LIFTED,
        by=actor,
        # outgoing_pause.resume() takes no reason, so an optional one only ever
        # reaches the incident log -- deliberate, not an oversight.
        reason=(body.reason or "").strip() or "Manual resume (operator mobile channel)",
    )
    return {"changed": True, **_pause_payload(after)}


@router.get("/history")
async def history(request: Request, limit: int = HISTORY_DEFAULT_LIMIT):
    """Recent kill-switch incidents (every arm and lift, manual or automatic).
    Read-only, so the session alone is enough -- no fresh TOTP required."""
    await require_operator_or_session(request)
    capped = max(1, min(limit, HISTORY_MAX_LIMIT))
    return {"limit": capped, "incidents": await kill_incident_log.list_incidents(limit=capped)}


class PushTokenBody(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    installation_id: str | None = Field(default=None, max_length=128)


@router.post("/push-token")
async def register_push_token(body: PushTokenBody, request: Request):
    """08/07 -- native push notifications follow-up to Item #201. Called at
    every app launch (idempotent upsert, see push_tokens.py), not just first
    install -- a reinstalled app gets a fresh Expo token that must replace
    the stale one."""
    await require_operator_or_session(request)
    from aria_core.push_tokens import register_push_token as _register_token
    await _register_token(body.token, body.installation_id)
    return {"ok": True}
