from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.access_code import verify_session
from app.auth.rate_limit import check_rate_limit
from app.auth.visitor import client_ip
from app.config import settings

PUBLIC_PREFIXES = (
    "/api/health",
    "/api/pulse",
    "/api/auth/",
    "/api/telegram/webhook",
    # x402-payable signals (app/x402_seller.py) -- machine-to-machine, gated by
    # x402's own on-chain payment challenge, not a Privy operator/member
    # session. Only reachable at all when x402_seller_ready() mounts the
    # router (gate OFF by default) -- exempting the prefix here is harmless
    # when unmounted, since FastAPI 404s a path with no matching route
    # regardless of this middleware.
    "/api/x402/",
    "/assets/",
    "/favicon.svg",
    "/icons.svg",
)

# Vanguard vitrine — visiteurs anonymes (sans session Privy)
VANGUARD_PUBLIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/api/aria/community-feedback"),
    ("POST", "/api/aria/chat"),
    ("GET", "/api/aria/content/site"),
    ("GET", "/api/aria/content/faq"),
    ("GET", "/api/aria/holding"),
    ("GET", "/api/aria/zhc/message/intro"),
    ("GET", "/api/aria/track-record"),
    ("GET", "/api/aria/exam-status"),
    ("GET", "/api/aria/sepolia-status"),
    ("GET", "/api/aria/relay/recent"),
    ("POST", "/api/aria/relay/reply"),
    # Diagnostics Claude Code (pool-status, agent-wallet-ledger) : même famille que
    # relay/* ci-dessus — gate dédié ARIA_DIAGNOSTIC_TOKEN interne à la route,
    # exempté ici du gate Privy/opérateur pour ne pas superposer deux logiques.
    ("GET", "/api/aria/diagnostics/pool-status"),
    ("GET", "/api/aria/diagnostics/agent-wallet-ledger"),
    ("GET", "/api/aria/diagnostics/paper-ledger"),
)


def _is_public(path: str, method: str = "GET") -> bool:
    if path == "/" or path == "/ws":
        return True
    if method == "GET" and path.startswith("/api/games/scores/") and path.endswith("/leaderboard"):
        return True
    if method == "GET" and path.startswith("/api/games/pot/") and path.endswith("/current"):
        return True
    upper = method.upper()
    for route_method, route_path in VANGUARD_PUBLIC_ROUTES:
        if upper == route_method and path == route_path:
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
            from aria_core.public_mode import is_operator_request

            # Source UNIQUE de vérité pour l'accès opérateur : secret admin (temps constant)
            # + second facteur TOTP si ADMIN_TOTP_SECRET est défini (2FA opt-in). Ainsi le
            # bypass opérateur du gate membre exige lui aussi le TOTP quand il est activé.
            if is_operator_request(request):
                return await call_next(request)

            token = _extract_token(request)
            if await verify_session(token):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "Member session required. Sign in with Privy on Aria Vanguard ZHC."},
            )

        return await call_next(request)


# #22 — endpoints publics /api/ (visiteurs anonymes, sans session Privy) exemptés du gate
# ci-dessus mais qui n'avaient jusqu'ici AUCUNE limite : content/faq, holding,
# zhc/message/intro, track-record, exam-status, sepolia-status, relay/recent, /api/health,
# /api/pulse, arena-signal/btc... -- une cible facile pour du scraping/bot abusif. /api/aria/
# chat et /api/aria/community-feedback ont déjà leur propre limiteur (par visiteur + par IP,
# cf. app/api/routes/aria.py) -- exemptés ici pour ne pas superposer deux logiques
# différentes, et consigne opérateur explicite de ne rien ajouter de plus sur le chat (#22).
# /api/auth/ (login/handoff) et /api/telegram/webhook ont leurs propres garde-fous
# (auth_rate_limit_*, secret Telegram) -- exemptés pour ne pas risquer de bloquer Telegram
# ou de doubler une logique de lockout déjà pensée pour l'auth.
#
# Complète, ne remplace pas, un pare-feu edge (Cloudflare WAF/rate-limiting) : le filet
# applicatif tourne toujours, même si l'edge tombe en panne ou n'est pas encore configuré
# (cf. docs/edge-firewall-cloudflare.md pour le volet DNS/WAF, hors de portée de ce dépôt).
_RATE_LIMIT_EXEMPT_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/aria/chat"),
        ("POST", "/api/aria/community-feedback"),
    }
)
_RATE_LIMIT_EXEMPT_PREFIXES = ("/api/auth/", "/api/telegram/webhook")


class PublicRateLimitMiddleware(BaseHTTPMiddleware):
    """Plafond par IP, partagé entre tous les endpoints publics /api/ ci-dessus."""

    async def dispatch(self, request: Request, call_next):
        if not settings.public_rate_limit_enabled:
            return await call_next(request)

        method = request.method.upper()
        if method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if (method, path) in _RATE_LIMIT_EXEMPT_ROUTES:
            return await call_next(request)
        if any(path.startswith(p) for p in _RATE_LIMIT_EXEMPT_PREFIXES):
            return await call_next(request)
        if not _is_public(path, method):
            # Route membre : déjà gardée par une session Privy (AccessCodeMiddleware),
            # hors cible de ce filet anti-scraping anonyme.
            return await call_next(request)

        ip = client_ip(request)
        if ip is None:
            # Pas de proxy-headers connu -> IP indéterminable ; on ne bloque jamais
            # à l'aveugle (même doctrine que check_rate_limit ailleurs dans le code).
            return await call_next(request)

        allowed = check_rate_limit(
            f"public_api_ip:{ip}",
            max_attempts=settings.public_rate_limit_attempts,
            window_seconds=settings.public_rate_limit_window_seconds,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — slow down."},
            )
        return await call_next(request)