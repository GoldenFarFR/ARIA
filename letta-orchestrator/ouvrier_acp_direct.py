"""Exécution directe workflows ACP — sans LLM ni pavé contexte."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aria_config import ARIA_REPO_ROOT


def _ensure_aria_core() -> None:
    core_src = ARIA_REPO_ROOT / "packages" / "aria-core" / "src"
    if core_src.is_dir():
        path = str(core_src)
        if path not in sys.path:
            sys.path.insert(0, path)


def try_acp_workflow_direct(message: str) -> str | None:
    """Crée, met à jour ou supprime une offre ACP — sans LLM. None si hors scope."""
    _ensure_aria_core()
    from aria_core.skills.acp_offering_skill import (
        execute_adhoc_workflow_create,
        execute_offering_delete,
        wants_acp_offering_delete,
        wants_adhoc_acp_workflow,
    )

    if wants_acp_offering_delete(message):
        reply, _ = asyncio.run(execute_offering_delete(message, "fr"))
        return reply
    if not wants_adhoc_acp_workflow(message):
        return None
    reply, _ = asyncio.run(execute_adhoc_workflow_create(message, "fr"))
    return reply