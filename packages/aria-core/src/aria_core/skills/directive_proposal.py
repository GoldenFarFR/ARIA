"""Câblage heartbeat -> canal de directives (`aria_directives.propose_directive`), pilote
autonome de la tâche #82.

Jusqu'ici `propose_directive` n'était appelé que depuis une commande Telegram opérateur
(`/canal propose`) -- ARIA elle-même n'avait aucun moyen autonome d'y déposer une
proposition. Ce module ajoute UNE seule source de signal, volontairement étroite pour un
premier pilote : un marqueur littéral `TODO(aria)` dans le code/docs du repo, jamais une
génération d'idées par LLM. Chaque candidat n'est proposé qu'UNE fois (mémorisé
localement).

Ce module ne modifie et ne contourne JAMAIS le gating de `aria_directives.py` -- il
appelle `propose_directive()` tel quel, avec une des 3 catégories déjà verrouillées par
`_DIRECTIVE_CATEGORIES` (jamais une catégorie choisie dynamiquement). Gaté OFF par défaut
via `ARIA_DIRECTIVE_PROPOSAL_ENABLED`, un 3e interrupteur indépendant de
`HeartbeatTask.enabled` et de `ARIA_DIRECTIVE_CHANNEL_ENABLED` (déjà OFF par défaut côté
producteur dans `propose_directive` lui-même).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]

_MARKER_RE = re.compile(r"TODO\(aria\)\s*:?\s*(.*)")

_EXCLUDE_DIR_NAMES = {
    ".venv", "node_modules", "__pycache__", ".git", "dist", "build", ".next",
}

# Mapping fermé, codé en dur : ce pilote ne reconnaît qu'un seul type de marqueur, mappé
# vers une seule catégorie déjà autorisée. Jamais de choix dynamique de catégorie ici --
# élargir exigerait un changement de code délibéré, comme pour _DIRECTIVE_CATEGORIES.
_MARKER_CATEGORY = "repo_hygiene"

# Un commentaire TODO(aria) arbitrairement long ne doit jamais atterrir tel quel dans la
# file -- titre/détail restent courts et lisibles en revue humaine.
_MAX_SNIPPET_LEN = 200

_TRUTHY = ("1", "true", "yes", "on")


def directive_proposal_enabled() -> bool:
    """Gate dédié (3e interrupteur, indépendant du gate producteur de propose_directive
    et du HeartbeatTask.enabled) -- OFF par défaut."""
    return os.environ.get("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "").strip().lower() in _TRUTHY


def _is_exempt_dir(name: str) -> bool:
    return name in _EXCLUDE_DIR_NAMES or name.startswith(".")


def _scan_todo_candidates() -> list[dict]:
    """Scan littéral (pas de LLM, pas d'heuristique de fraîcheur) des marqueurs
    `TODO(aria)` sous la racine du repo. Renvoie une liste ordonnée de candidats
    {key, path, line, text}."""
    candidates: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if not _is_exempt_dir(d)]
        for filename in sorted(filenames):
            if not (filename.endswith(".py") or filename.endswith(".md")):
                continue
            file_path = Path(dirpath) / filename
            rel = file_path.relative_to(REPO_ROOT).as_posix()
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                match = _MARKER_RE.search(line)
                if not match:
                    continue
                text = match.group(1).strip() or line.strip()
                candidates.append(
                    {
                        "key": f"{rel}:{lineno}",
                        "path": rel,
                        "line": lineno,
                        "text": text,
                    }
                )
    candidates.sort(key=lambda c: c["key"])
    return candidates


async def _ensure_seen_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS directive_proposal_seen ("
        "candidate_key TEXT PRIMARY KEY, proposed_at TEXT NOT NULL)"
    )
    await db.commit()


async def _already_seen(candidate_key: str) -> bool:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_seen_table(db)
        cursor = await db.execute(
            "SELECT 1 FROM directive_proposal_seen WHERE candidate_key = ?", (candidate_key,)
        )
        row = await cursor.fetchone()
    return row is not None


async def _mark_seen(candidate_key: str) -> None:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_seen_table(db)
        await db.execute(
            "INSERT OR IGNORE INTO directive_proposal_seen (candidate_key, proposed_at) VALUES (?, ?)",
            (candidate_key, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _pick_next_candidate(candidates: list[dict]) -> dict | None:
    for candidate in candidates:
        if not await _already_seen(candidate["key"]):
            return candidate
    return None


async def run_directive_proposal_cycle(*, notifier=None, scanner=None) -> dict:
    """Un tour : repère UN candidat `TODO(aria)` non encore proposé et appelle
    `propose_directive` (catégorie fixe `repo_hygiene`). Fail-closed à chaque étage --
    jamais de proposition en lot, jamais de catégorie choisie dynamiquement."""
    if not directive_proposal_enabled():
        return {"outcome": "skipped_disabled"}

    if scanner is None:
        scanner = _scan_todo_candidates

    candidates = scanner()
    candidate = await _pick_next_candidate(candidates)
    if candidate is None:
        return {"outcome": "nothing_new"}

    from aria_core.aria_directives import propose_directive

    snippet = candidate["text"][:_MAX_SNIPPET_LEN]
    title = f"TODO(aria) dans {candidate['path']}:{candidate['line']}"[:_MAX_SNIPPET_LEN]
    detail = f"{candidate['path']}:{candidate['line']} -- {snippet}"[:_MAX_SNIPPET_LEN]
    result = await propose_directive(_MARKER_CATEGORY, title, detail)

    if not result.get("ok"):
        return {"outcome": "skipped", "reason": result.get("reason"), "path": candidate["path"]}

    await _mark_seen(candidate["key"])

    if notifier:
        try:
            await notifier(f"📋 Directive auto-proposée par ARIA -- {title}")
        except Exception:  # noqa: BLE001 -- une notification en échec ne casse jamais le cycle
            pass

    return {
        "outcome": "ok",
        "category": result["category"],
        "title": result["title"],
        "id": result["id"],
        "path": candidate["path"],
    }
