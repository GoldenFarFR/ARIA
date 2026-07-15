from __future__ import annotations

from fastapi import HTTPException, Request


def visitor_id_from_request(request: Request) -> str | None:
    vid = request.headers.get("X-Visitor-Id", "").strip()
    if len(vid) < 8:
        return None
    return vid[:64]


def require_visitor_id(request: Request) -> str:
    vid = visitor_id_from_request(request)
    if not vid:
        raise HTTPException(status_code=400, detail="X-Visitor-Id header required")
    return vid


_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


def client_ip(request: Request) -> str | None:
    """IP client RÉELLE, ou None si indéterminable.

    Derrière nginx, `request.client.host` vaut le loopback SAUF si uvicorn tourne
    avec `--proxy-headers` (il traduit alors X-Forwarded-For fourni par nginx). On ne
    renvoie JAMAIS le loopback : sinon un plafond « par IP » deviendrait un bucket global
    qui verrouillerait TOUS les visiteurs d'un coup. None => l'appelant saute simplement
    le plafond IP (pas de régression), et sur le VPS (proxy-headers) il obtient la vraie IP.
    """
    host = request.client.host if request.client else None
    if not host or host in _LOOPBACK:
        return None
    return host