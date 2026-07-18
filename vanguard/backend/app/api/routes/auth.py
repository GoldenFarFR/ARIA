from fastapi import APIRouter, HTTPException, Request
from starlette.responses import RedirectResponse

from aria_core.holding import holding_name
from aria_core.narrative import welcome_site_access, welcome_site_return
from app.auth.access_code import create_session, purge_expired, verify_session
from app.auth.privy_sessions import bearer_token as _bearer_token
from app.auth.privy_sessions import login_with_privy, lookup_linked_handle
from app.auth.privy_verify import (
    PrivyAuthError,
    extract_member_from_identity,
    privy_configured,
    privy_did_from_access,
)
from app.config import settings
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthStatusResponse(BaseModel):
    required: bool
    message: str
    site_name: str = "Aria Market"
    holding_name: str = "Aria Vanguard ZHC"


class PrivyTokenRequest(BaseModel):
    access_token: str
    identity_token: str | None = None


class PrivyLoginResponse(BaseModel):
    token: str
    expires_at: str
    twitter_username: str | None = None
    message: str


def _session_token(request: Request, authorization: str | None = None) -> str | None:
    return _bearer_token(authorization) or request.cookies.get("aria_market_token")


def _spa_base_url(request: Request) -> str:
    explicit = settings.public_site_url.strip().rstrip("/")
    if explicit and not explicit.startswith("http://localhost"):
        return explicit
    base = str(request.base_url).rstrip("/")
    if base.endswith("/api"):
        return base[:-4]
    return base


def _handoff_redirect(request: Request, token: str) -> RedirectResponse:
    """Bootstrap SPA localStorage (WebSocket) and HttpOnly cookie (API)."""
    base = _spa_base_url(request)
    response = RedirectResponse(url=f"{base}/?aria_token={token}", status_code=302)
    secure = request.url.scheme == "https"
    response.set_cookie(
        key="aria_market_token",
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return response


class PrivyConfigResponse(BaseModel):
    app_id: str | None = None
    configured: bool = False


@router.get("/privy/config", response_model=PrivyConfigResponse)
async def privy_public_config():
    app_id = (settings.privy_app_id or "").strip() or None
    return PrivyConfigResponse(app_id=app_id, configured=privy_configured())


@router.get("/required", response_model=AuthStatusResponse)
async def auth_required():
    if settings.access_code_enabled:
        message = (
            f"Sign in with your X-linked email on {holding_name()} to access "
            f"{settings.app_name}."
        )
        return AuthStatusResponse(
            required=True,
            message=message,
            site_name=settings.app_name,
            holding_name=holding_name(),
        )

    public_msg = (
        "ARIA is open to everyone — public intelligence for the holding and Aria Market."
        if settings.aria_public_mode
        else "Open access (development mode)."
    )
    return AuthStatusResponse(
        required=False,
        message=public_msg,
        site_name=settings.app_name,
        holding_name=holding_name(),
    )


@router.get("/handoff")
async def auth_handoff(request: Request, token: str | None = None):
    """Vanguard → Aria Market: validate member session, set cookie, redirect to SPA."""
    if not settings.access_code_enabled:
        return RedirectResponse(url="/", status_code=302)

    raw = (token or request.query_params.get("aria_token") or "").strip()
    if not raw or not await verify_session(raw):
        return RedirectResponse(url=f"{_spa_base_url(request)}/", status_code=302)

    return _handoff_redirect(request, raw)


@router.get("/session")
async def check_session(request: Request, authorization: str | None = None):
    token = _session_token(request, authorization) or request.query_params.get("aria_token")

    if not settings.access_code_enabled:
        return {"valid": True, "mode": "development"}

    valid = await verify_session(token)
    body: dict[str, object] = {"valid": valid}
    if valid and token:
        body["token"] = token
    return body


@router.post("/privy/login", response_model=PrivyLoginResponse)
async def privy_login(body: PrivyTokenRequest):
    if not privy_configured():
        raise HTTPException(status_code=503, detail="Privy member login is not configured.")

    try:
        privy_did = privy_did_from_access(body.access_token)
        identity = (body.identity_token or "").strip()
        if identity:
            id_did, member_handle = extract_member_from_identity(identity)
            if id_did != privy_did:
                raise PrivyAuthError("Privy token mismatch")
        else:
            member_handle = await lookup_linked_handle(privy_did)
            if not member_handle:
                raise PrivyAuthError(
                    "Première connexion : Privy Dashboard → Authentication → Advanced → "
                    "activer « Return user data in an identity token », puis réessaie."
                )
        token, expires, is_new_member = await login_with_privy(
            privy_did=privy_did,
            twitter_username=member_handle,
            ttl_hours=settings.session_ttl_hours,
        )
    except PrivyAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return PrivyLoginResponse(
        token=token,
        expires_at=expires.isoformat(),
        twitter_username=member_handle,
        message=welcome_site_access() if is_new_member else welcome_site_return(),
    )


@router.post("/dev-session")
async def dev_session():
    if settings.access_code_enabled:
        raise HTTPException(status_code=403, detail="Forbidden")
    await purge_expired()
    token, expires = await create_session()
    return {"token": token, "expires_at": expires.isoformat()}