"""Préflight mémoire aria-core pour l'ouvrier Groq (arbitre + vector + COLLEGUE)."""
from __future__ import annotations

import asyncio
import os
import sys
from functools import lru_cache
from pathlib import Path

from aria_config import ARIA_REPO_ROOT, bridge_api_keys

_MEMORY_BUDGET = 4500
_BOOTSTRAPPED = False


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _ensure_aria_core_path() -> None:
    core_src = ARIA_REPO_ROOT / "packages" / "aria-core" / "src"
    if core_src.is_dir():
        path = str(core_src)
        if path not in sys.path:
            sys.path.insert(0, path)


def bootstrap_aria_core_runtime() -> None:
    """Configure DATA_DIR + flags mémoire (idempotent)."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _ensure_aria_core_path()
    bridge_api_keys()

    data_raw = os.environ.get("DATA_DIR", "").strip()
    if data_raw:
        data_dir = Path(data_raw)
    else:
        data_dir = ARIA_REPO_ROOT / "vanguard" / "backend" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATA_DIR", str(data_dir))
    os.environ.setdefault("ARIA_REPO_ROOT", str(ARIA_REPO_ROOT))
    os.environ.setdefault("ARIA_VECTOR_MEMORY", "true")
    os.environ.setdefault("ARIA_MEMORY_ARBITRATOR", "true")
    os.environ.setdefault("ARIA_PUBLIC_MODE", "false")

    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=_flag("ARIA_VECTOR_MEMORY", True),
            aria_memory_arbitrator=_flag("ARIA_MEMORY_ARBITRATOR", True),
            aria_ddg_search_cache=_flag("ARIA_DDG_SEARCH_CACHE", False),
            aria_public_mode=False,
            aria_llm_enabled=False,
            debug=True,
        ),
    )
    _BOOTSTRAPPED = True


async def _fetch_context_async(query_hint: str) -> str:
    bootstrap_aria_core_runtime()
    from aria_core import repertoire_db
    from aria_core.memory.llm_context import build_llm_context

    try:
        await repertoire_db.init_repertoire_db()
    except Exception:
        pass
    ctx = await build_llm_context(public=False, query_hint=(query_hint or "")[:500])
    return (ctx or "").strip()


def preflight_memory_context(query_hint: str) -> str:
    """Bloc mémoire injecté avant Groq — arbitre court/moyen/long inclus."""
    if os.environ.get("ARIA_OUVRIER_MEMORY", "").strip().lower() in ("0", "false", "no", "off"):
        return ""
    try:
        ctx = asyncio.run(_fetch_context_async(query_hint))
    except Exception as exc:
        return f"(mémoire aria-core indisponible : {exc})"
    if not ctx:
        return ""
    if len(ctx) > _MEMORY_BUDGET:
        ctx = ctx[:_MEMORY_BUDGET] + "\n… (mémoire tronquée pour quota contexte ouvrier)"
    return (
        "MÉMOIRE ARIA-CORE (SSOT — arbitre court/moyen/long, COLLEGUE, vector opt-in).\n"
        "Utilise ces faits ; ne contredis pas une directive ou un pitfall sans preuve.\n\n"
        f"{ctx}"
    )


@lru_cache(maxsize=1)
def memory_status_line() -> str:
    """Une ligne diagnostic (trace)."""
    bootstrap_aria_core_runtime()
    try:
        from aria_core.memory.arbitrator import is_arbitrator_enabled
        from aria_core.memory.vector import is_vector_enabled

        arb = "on" if is_arbitrator_enabled() else "off"
        vec = "on" if is_vector_enabled() else "off"
        return f"mémoire ouvrier : arbitre={arb} vector={vec}"
    except Exception as exc:
        return f"mémoire ouvrier : erreur ({exc})"