from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.access_code import verify_session
from app.config import settings

PUBLIC_PREFIXES = (
    "/api/health",
    "/api/auth/",
    "/api/billing/webhook",
    "/api/billing/plan",
    "/api/telegram/webhook",
    "/assets/",
    "/favicon.svg",
    "/icons.svg",
)


def _is_public(path: str, method: str = "GET") -> bool:
    if path == "/" or path == "/ws":
        return True
    if method == "GET" and path.startswith("/api/games/scores/") and path.endswith("/leaderboard"):
        return True
    if method == "GET" and path.startswith("/api/games/pot/") and path.endswith("/current"):
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.query_params.get("token")
        or request.query_params.get("aria_token")
        or request.cookies.get("aria_market_token")
    )





class AccessCodeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.access_code_enabled:
            return await call_next(request)

        path = request.url.path

        if _is_public(path, request.method):
            return await call_next(request)

        if path.startswith("/api/"):
            admin_secret = (settings.admin_api_secret or "").strip()
            provided_admin = (request.headers.get("X-Admin-Secret") or "").strip()
            if admin_secret and provided_admin == admin_secret:
                return await call_next(request)

            token = _extract_token(request)
            if await verify_session(token):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "Member session required. Sign in with Privy on Aria Vanguard ZHC."},
            )

        return await call_next(request)