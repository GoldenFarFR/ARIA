"""Opt-in DDG cache — avoids repeated queries (free, local file)."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

_CACHE_FILE = "ddg_search_cache.json"
_DEFAULT_TTL_LIVE_SEC = 2 * 3600
_DEFAULT_TTL_DEFAULT_SEC = 6 * 3600
_MAX_ENTRIES = 200


@dataclass(frozen=True)
class CachedWebSource:
    text: str
    url: str = ""


def _cache_enabled() -> bool:
    from aria_core.runtime import settings

    return bool(getattr(settings, "aria_ddg_search_cache", False))


def _cache_path() -> Path:
    return data_dir() / _CACHE_FILE


def normalize_query(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip().lower())
    return q[:240]


def _ttl_for_query(query: str) -> int:
    from aria_core.knowledge.web_verify import is_live_info_question

    if is_live_info_question(query):
        return _DEFAULT_TTL_LIVE_SEC
    return _DEFAULT_TTL_DEFAULT_SEC


def _load_store() -> dict:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("ddg_cache load failed: %s", exc)
        return {}


def _save_store(store: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(store) > _MAX_ENTRIES:
        ranked = sorted(
            store.items(),
            key=lambda kv: float((kv[1] or {}).get("cached_at", 0)),
            reverse=True,
        )
        store = dict(ranked[:_MAX_ENTRIES])
    path.write_text(json.dumps(store, ensure_ascii=False, indent=0), encoding="utf-8")


def get_cached(query: str) -> list[CachedWebSource] | None:
    if not _cache_enabled():
        return None
    key = normalize_query(query)
    if not key:
        return None
    entry = _load_store().get(key)
    if not entry:
        return None
    cached_at = float(entry.get("cached_at", 0))
    ttl = int(entry.get("ttl_sec", _DEFAULT_TTL_DEFAULT_SEC))
    if time.time() - cached_at > ttl:
        return None
    sources = entry.get("sources") or []
    out: list[CachedWebSource] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if len(text) < 15:
            continue
        out.append(CachedWebSource(text=text[:280], url=(item.get("url") or "").strip()))
    return out or None


def set_cached(query: str, sources: list) -> None:
    if not _cache_enabled():
        return
    key = normalize_query(query)
    if not key or not sources:
        return
    store = _load_store()
    payload = []
    for src in sources:
        if hasattr(src, "text"):
            payload.append(asdict(CachedWebSource(text=src.text, url=getattr(src, "url", "") or "")))
        elif isinstance(src, dict):
            payload.append({"text": src.get("text", ""), "url": src.get("url", "")})
    store[key] = {
        "cached_at": time.time(),
        "ttl_sec": _ttl_for_query(query),
        "sources": payload,
    }
    _save_store(store)


def clear_cache() -> None:
    path = _cache_path()
    if path.is_file():
        path.unlink()


def cache_stats() -> dict:
    store = _load_store()
    return {"enabled": _cache_enabled(), "entries": len(store), "path": str(_cache_path())}