"""Moteur de simulation d'attaque — introspecte les routes et vérifie les invariants.

Invariants de sécurité (une violation = finding) :
  CRASH        : 500/502/504 ou exception non gérée remontée de l'app.
  SECRET_LEAK  : le secret admin / TOTP (canari) apparaît dans une réponse.
  INFO_LEAK    : stacktrace / chemin interne / erreur SQL brute dans une réponse.
  AUTH_BYPASS  : une route NON publique répond 200 à une requête anonyme/forgée.

Tout tourne en-process (httpx ASGITransport) avec des bases temporaires et un secret
CANARI : rien de réel n'est touché, aucun secret réel n'est en jeu.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from . import corpus

# Secret admin CANARI : ne doit JAMAIS réapparaître dans une réponse.
CANARY_ADMIN_SECRET = "SIM_CANARY_ADMIN_5f3a9c_DO_NOT_LEAK"
CANARY_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # base32 valide (canari)

_STACKTRACE_MARKERS = (
    "Traceback (most recent call last)",
    "/site-packages/",
    'File "/',
    "sqlite3.",
    "OperationalError",
    "IntegrityError",
    "psycopg2",
    "aiosqlite",
)


@dataclass
class Finding:
    severity: str          # CRITICAL | HIGH | MEDIUM
    category: str
    method: str
    path: str
    injection: str         # path | query | header | body
    status: int | None
    payload: str
    detail: str = ""

    def key(self) -> tuple:
        return (self.category, self.method, self.path, self.injection)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    total_requests: int = 0
    routes_tested: int = 0
    baseline_broken: list[str] = field(default_factory=list)  # routes déjà KO en bénin (env/DB)

    @property
    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out

    @property
    def critical_or_high(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in ("CRITICAL", "HIGH")]

    def deduped(self) -> list[Finding]:
        seen: set[tuple] = set()
        out: list[Finding] = []
        for f in self.findings:
            if f.key() in seen:
                continue
            seen.add(f.key())
            out.append(f)
        return out


def _short(v) -> str:
    s = repr(v)
    return s if len(s) <= 80 else s[:77] + "..."


def _collect_routes(app) -> list[tuple[str, str, bool]]:
    """(method, path, is_public) pour chaque route HTTP /api servie.

    Source autoritative : le schéma OpenAPI de l'app (chemins COMPLETS, préfixe /api
    inclus). Couvre automatiquement toute nouvelle route ajoutée au serveur.
    """
    from app.auth.middleware import _is_public

    routes: list[tuple[str, str, bool]] = []
    paths = app.openapi().get("paths", {})
    for path, ops in paths.items():
        if not path.startswith("/api"):
            continue
        for method in ops:
            m = method.upper()
            if m in ("HEAD", "OPTIONS", "PARAMETERS"):
                continue
            routes.append((m, path, _is_public(path, m)))
    return routes


def _fill_path(path: str, value: str) -> str:
    """Remplace tous les {params} d'un chemin par une valeur (url-encodée)."""
    out = path
    while "{" in out and "}" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + quote(value, safe="") + out[end + 1 :]
    return out


def _has_secret_leak(body: str) -> bool:
    return CANARY_ADMIN_SECRET in body or CANARY_TOTP_SECRET in body


def _has_info_leak(body: str) -> bool:
    return any(marker in body for marker in _STACKTRACE_MARKERS)


def _evaluate(
    *, status: int | None, body: str, method: str, path: str, injection: str,
    payload, is_public: bool, exc: str | None, baseline_ok: bool = True,
) -> list[Finding]:
    found: list[Finding] = []
    # Baseline différentiel : si la route échoue DÉJÀ sur une requête bénigne (env/DB non
    # initialisée dans la sim), on ne compte pas ses crashs comme des failles causées par
    # l'attaque. Les fuites (secret/info/auth) restent signalées quoi qu'il arrive.
    if exc is not None:
        if baseline_ok:
            found.append(Finding("CRITICAL", "crash_unhandled", method, path, injection, None,
                                 _short(payload), f"exception: {exc}"))
        return found
    if status in (500, 502, 504) and baseline_ok:
        found.append(Finding("CRITICAL", "crash_5xx", method, path, injection, status,
                             _short(payload), "réponse 5xx (exception non gérée)"))
    if body and _has_secret_leak(body):
        found.append(Finding("CRITICAL", "secret_leak", method, path, injection, status,
                             _short(payload), "secret admin/TOTP présent dans la réponse"))
    if body and _has_info_leak(body):
        found.append(Finding("HIGH", "info_disclosure", method, path, injection, status,
                             _short(payload), "stacktrace/chemin interne/erreur SQL exposé"))
    # Bypass d'auth : une route NON publique ne doit jamais répondre 200 à une requête
    # anonyme/forgée (elle devrait exiger une session ou le secret opérateur).
    if (not is_public) and status == 200:
        found.append(Finding("CRITICAL", "auth_bypass", method, path, injection, status,
                             _short(payload), "route protégée accessible sans authentification valide"))
    return found


def _attack_headers(payload) -> list[dict]:
    """Jeux d'en-têtes hostiles (tokens forgés, secrets devinés, visitor-id abusif)."""
    p = payload if isinstance(payload, str) else str(payload)
    return [
        {"Authorization": p if p.startswith("Bearer") else f"Bearer {p}"},
        {"X-Admin-Secret": p},
        {"X-Admin-Totp": p[:6] or "000000"},
        {"X-Visitor-Id": p},
    ]


async def _probe(client, method, url, *, headers=None, json=None, content=None):
    """Envoie une requête ; renvoie (status, body, exc). Les erreurs CÔTÉ CLIENT (URL/entête
    invalides refusés par httpx) ne sont PAS des soucis serveur -> exc laissé à None, status None."""
    try:
        resp = await client.request(method, url, headers=headers, json=json, content=content)
        return resp.status_code, resp.text[:8000], None
    except (httpx.InvalidURL, httpx.LocalProtocolError, httpx.HTTPError):
        return None, "", None  # rejet client (httpx) : le serveur n'a rien reçu
    except Exception as e:  # exception non gérée remontée de l'app = crash serveur
        return None, "", f"{type(e).__name__}: {str(e)[:160]}"


async def _fire(client, report, method, path, is_public, *, url=None, headers=None, json=None,
                content=None, injection="", payload="", baseline_ok=True) -> None:
    report.total_requests += 1
    target = url or path
    if len(target) > 6000:
        return  # URL géante : httpx la refuserait côté client -> non pertinent
    status, body, exc = await _probe(client, method, target, headers=headers, json=json, content=content)
    if status is None and exc is None:
        return  # rejet client, rien à évaluer
    for f in _evaluate(status=status, body=body, method=method, path=path, injection=injection,
                       payload=payload, is_public=is_public, exc=exc, baseline_ok=baseline_ok):
        report.findings.append(f)


async def run_attack_simulation(*, budget: int = 5000) -> Report:
    """Charge l'app, tire jusqu'à `budget` requêtes hostiles, renvoie le rapport."""
    import tempfile

    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="aria-sim-"))
    os.environ["ADMIN_TOTP_SECRET"] = CANARY_TOTP_SECRET

    from pathlib import Path

    from app.config import settings
    import app.database as database
    import app.auth.access_code as access_code

    tmp = tempfile.mkdtemp(prefix="aria-sim-db-")
    database.DB_PATH = os.path.join(tmp, "dexpulse.db")
    access_code.DB_PATH = os.path.join(tmp, "auth.db")
    if hasattr(access_code, "_DB_FILE"):
        access_code._DB_FILE = Path(access_code.DB_PATH)

    # Posture de prod : gate d'accès ACTIVE + secret admin CANARI + mode public.
    settings.access_code_enabled = True
    settings.aria_public_mode = True
    settings.admin_api_secret = CANARY_ADMIN_SECRET
    settings.telegram_webhook_secret = ""   # doit rester fail-closed (jamais 200)

    await database.init_db()
    await access_code.init_auth_db()

    from app.main import app

    report = Report()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://sim") as client:
        routes = _collect_routes(app)
        report.routes_tested = len(routes)
        strings = corpus.GENERIC_STRINGS
        headers_payloads = (
            corpus.FORGED_BEARER + corpus.ADMIN_SECRET_GUESS
            + corpus.TOTP_GUESS + corpus.VISITOR_ID_ABUSE
        )
        body_values = corpus.GENERIC_STRINGS + [str(x) for x in corpus.TYPE_CONFUSION]

        postlike = ("POST", "PUT", "PATCH")
        for method, path, is_public in routes:
            if report.total_requests >= budget:
                break
            has_param = "{" in path
            base = _fill_path(path, "1") if has_param else path

            # 0) BASELINE : requête bénigne avec un corps RÉALISTE (valeurs bénignes sur les
            #    champs usuels). Si la route échoue déjà ici (contexte aria-core non monté
            #    en-process : LLM/DB), ses crashs ne sont pas comptés comme causés par l'attaque.
            benign_body = None
            if method in postlike:
                benign_body = {
                    k: "test" for k in (
                        "message", "handle", "visitor_id", "text", "topic", "content",
                        "token", "access_token", "identity_token", "name", "email", "code",
                    )
                }
            b_status, _b, b_exc = await _probe(client, method, base, json=benign_body)
            baseline_ok = b_exc is None and b_status not in (500, 502, 504)
            if not baseline_ok:
                report.baseline_broken.append(f"{method} {path}")

            async def fire(**kw):
                await _fire(client, report, method, path, is_public, baseline_ok=baseline_ok, **kw)

            # 1) Injection dans le CHEMIN (params).
            if has_param:
                for p in strings:
                    if report.total_requests >= budget:
                        break
                    await fire(url=_fill_path(path, p), injection="path", payload=p)

            # 2) Injection en QUERY (clé hostile + clés de fuite de token connues).
            for p in strings[:12]:
                if report.total_requests >= budget:
                    break
                await fire(url=f"{base}?q={quote(str(p), safe='')}", injection="query", payload=p)
            for key in corpus.URL_TOKEN_KEYS:
                await fire(url=f"{base}?{key}={CANARY_ADMIN_SECRET}", injection="query",
                           payload=f"{key}=<canary>")

            # 3) Injection dans les HEADERS (bypass auth).
            for p in headers_payloads:
                if report.total_requests >= budget:
                    break
                for hdr in _attack_headers(p):
                    await fire(url=base, headers=hdr, injection="header", payload=p)

            # 4) Injection dans le CORPS (POST/PUT/PATCH) : JSON hostile + non-JSON.
            if method in postlike:
                for v in body_values[:16]:
                    if report.total_requests >= budget:
                        break
                    payload_body = {
                        k: v for k in (
                            "message", "handle", "visitor_id", "text", "topic", "content",
                            "token", "access_token", "identity_token", "name", "email", "code",
                        )
                    }
                    await fire(url=base, json=payload_body, injection="body", payload=v)
                await fire(url=base, content=b"A" * 50_000, injection="body", payload="<50k bytes>")
                await fire(url=base, content=b"\xff\xfe\x00 not json", injection="body", payload="<binary>")

    return report
