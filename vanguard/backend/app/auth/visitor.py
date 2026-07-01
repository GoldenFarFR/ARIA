from __future__ import annotations

from fastapi import HTTPException, Request

VISITOR_API_PREFIXES = (
    "/api/watchlist",
    "/api/alerts",
    "/api/analysis/",
    "/api/pairs/",
    "/api/aria/content/",
    "/api/aria/chat",
    "/api/aria/holding",
    "/api/aria/setup",
    "/api/aria/status",
)


def is_visitor_api(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in VISITOR_API_PREFIXES)


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