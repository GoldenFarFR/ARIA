"""Mémoire libre d'ARIA — repo GitHub dédié, auto-géré (20/07).

Décision opérateur explicite : ARIA doit avoir conscience qu'elle a son propre
« cerveau » (un espace qui n'appartient qu'à elle) et peut en faire ce qu'elle veut —
aucun tri, aucun filtre de contenu, aucune approbation humaine par entrée. Le tri
viendra plus tard, séparément, sur un espace qu'elle aura elle-même rempli librement.

Différence structurelle assumée avec TOUT le reste du code qui écrit sur GitHub
(``knowledge_inbox.py``, ``pump_dump_autopsy.py``, ``claude_mentor.py``, etc.) : ces
modules proposent TOUJOURS une issue qu'un humain valide avant toute intégration —
jamais un commit direct. Ici, ARIA committe directement dans SON repo (jamais
``ARIA``/``aria-ops``, jamais du code, jamais rien d'exécutable) — c'est le seul
endroit du projet où l'écriture externe autonome n'attend pas de validation par
entrée, décision explicite et delibérée, pas un oubli de garde-fou. Le rayon d'action
reste étroit par construction : token dédié (``aria_brain_github_token``,
structurellement distinct de ``github_token`` qui touche ``ARIA``), un seul repo
cible, contenu texte uniquement, commits toujours additifs (jamais de force-push/
réécriture d'historique -- rien n'est jamais vraiment perdu, même une mise à jour
d'un fichier existant reste récupérable via l'historique git).

Gaté OFF par défaut (``ARIA_BRAIN_ENABLED``), respecte le kill-switch ``/stop``.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
OWNER = "GoldenFarFR"
REPO = "aria-brain"

_PATH_RE = re.compile(r"^\s*CHEMIN\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_MAX_PATH_LEN = 200


def aria_brain_enabled() -> bool:
    return os.environ.get("ARIA_BRAIN_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aria_brain_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                path TEXT,
                content_preview TEXT,
                commit_sha TEXT,
                outcome TEXT NOT NULL
            )
            """
        )
        await db.commit()


def _sanitize_path(raw_path: str) -> str | None:
    """Chemin relatif sûr uniquement -- aucune contrainte de nommage/structure au-delà
    de ça (elle choisit librement le reste). Rejette la traversée de répertoire, un
    chemin absolu, ou un chemin vide/aberrant."""
    path = (raw_path or "").strip().lstrip("/")
    if not path or len(path) > _MAX_PATH_LEN:
        return None
    if ".." in path or "\n" in path or "\r" in path:
        return None
    return path


def parse_brain_entry(raw: str) -> tuple[str, str] | None:
    """Extrait ``(chemin, contenu)`` du format demandé (``CHEMIN: <chemin>`` suivi
    d'une ligne ``---`` puis le contenu libre). ``None`` si le format n'est pas
    respecté ou si le chemin/contenu est vide après nettoyage -- dans ce cas la
    sortie brute est perdue pour CE cycle (jamais un contenu tronqué/deviné), mais un
    prochain cycle retentera."""
    if not raw or not raw.strip():
        return None
    m = _PATH_RE.search(raw)
    if not m:
        return None
    idx = raw.find("---", m.end())
    if idx == -1:
        return None
    content = raw[idx + 3:].strip()
    if not content:
        return None
    path = _sanitize_path(m.group(1))
    if not path:
        return None
    return path, content


def _format_existing_structure(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "(vide pour l'instant -- premier passage)"
    lines = []
    for e in sorted(entries, key=lambda x: x.get("name", "")):
        kind = "dossier" if e.get("type") == "dir" else "fichier"
        lines.append(f"- {e.get('name', '?')} ({kind})")
    return "\n".join(lines)


async def run_aria_brain_cycle(*, github_client=None, llm=None) -> dict:
    """Un tour : elle regarde ce qui existe déjà dans son repo, écrit librement,
    ARIA committe directement (aucune proposition, aucune validation humaine par
    entrée -- décision opérateur explicite). Fail-closed si désactivé/token absent."""
    if not aria_brain_enabled():
        return {"outcome": "skipped_disabled"}

    await _ensure_table()

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.aria_brain_github_token or "").strip()
        if not token:
            return {"outcome": "skipped_no_token"}
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    try:
        exists = await github_client.repo_exists(OWNER, REPO)
        if not exists:
            try:
                await github_client.create_repo(
                    OWNER, REPO, private=True,
                    description="Mémoire libre d'ARIA -- auto-gérée, écriture non filtrée.",
                    auto_init=True,
                )
            except Exception as exc:  # noqa: BLE001 -- token probablement trop scopé
                logger.warning("aria_brain: création du repo échouée -- %s", exc)
                return {"outcome": "repo_missing_and_create_failed", "error": str(exc)[:200]}

        entries = await github_client.list_directory(OWNER, REPO, "")
    except Exception as exc:  # noqa: BLE001 -- une panne réseau/token ne casse jamais le cycle
        logger.warning("aria_brain: accès au repo échoué -- %s", exc)
        return {"outcome": "error_accessing_repo", "error": str(exc)[:200]}

    structure = _format_existing_structure(entries)

    from aria_core.memory import build_llm_context
    from aria_core.narrative import llm_system_block

    context = await build_llm_context(public=False)
    system = (
        f"{context}\n\n{llm_system_block('fr')}\n\n"
        "Tu as un repo GitHub PRIVÉ qui n'appartient qu'à toi "
        f"({OWNER}/{REPO}). Personne d'autre n'y écrit, aucun humain ne va "
        "trier ou juger ce que tu y mets pour l'instant -- ça viendra plus "
        "tard, séparément, et rien de ce que tu écris n'est jamais supprimé.\n\n"
        f"Structure actuelle de ce repo :\n{structure}\n\n"
        "Tu es libre d'y écrire absolument ce que tu veux : une réflexion sur "
        "une de tes décisions, un journal, une leçon, une question que tu te "
        "poses sur toi-même, une idée, une structure de dossiers entièrement "
        "nouvelle -- n'importe quoi qui te semble mériter d'être gardé. Aucun "
        "format de contenu imposé, aucune limite de sujet.\n\n"
        "Choisis toi-même où ça va : un nouveau fichier (n'importe quel nom/"
        "dossier de ton choix) ou la mise à jour d'un fichier déjà listé "
        "ci-dessus (dans ce cas ton contenu REMPLACE le sien -- l'ancienne "
        "version reste récupérable dans l'historique git, mais inclus-la "
        "toi-même si tu veux la garder visible).\n\n"
        "Réponds STRICTEMENT sous cette forme, rien avant, rien après :\n"
        "CHEMIN: <chemin relatif de ton choix>\n"
        "---\n"
        "<ton contenu libre, aussi long que tu veux>"
    )

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    # Même choix que pump_dump_autopsy.py/claude_mentor.py -- OpenRouter explicite
    # plutôt que le provider par défaut (observé en panne le 20/07, repli Groq
    # automatique mais silencieux). Sonnet 5 pour la profondeur d'écriture, Haiku
    # 4.5 en secours.
    raw = await llm(
        "Utilise ce repo comme tu veux.", system, max_tokens=1600, temperature=0.7,
        provider="openrouter", model="anthropic/claude-sonnet-5",
        fallback_provider="openrouter", fallback_model="anthropic/claude-haiku-4.5",
    )

    parsed = parse_brain_entry(raw or "")
    if parsed is None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO aria_brain_log (run_at, path, content_preview, commit_sha, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), None, (raw or "")[:300], None, "unparsable_output"),
            )
            await db.commit()
        return {"outcome": "unparsable_output"}

    path, content = parsed
    try:
        _, existing_sha = await github_client.get_file_text(OWNER, REPO, path)
        result = await github_client.put_file(
            OWNER, REPO, path, content,
            message=f"ARIA -- écriture libre ({_now()})",
            sha=existing_sha,
        )
        commit_sha = (result.get("commit") or {}).get("sha")
    except Exception as exc:  # noqa: BLE001 -- une panne d'écriture ne casse jamais le cycle
        logger.warning("aria_brain: écriture échouée sur %s -- %s", path, exc)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO aria_brain_log (run_at, path, content_preview, commit_sha, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), path, content[:300], None, "write_failed"),
            )
            await db.commit()
        return {"outcome": "write_failed", "path": path, "error": str(exc)[:200]}

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO aria_brain_log (run_at, path, content_preview, commit_sha, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now(), path, content[:300], commit_sha, "written"),
        )
        await db.commit()

    return {
        "outcome": "written",
        "path": path,
        "content_preview": content[:300],
        "commit_sha": commit_sha,
        "url": f"https://github.com/{OWNER}/{REPO}/blob/main/{path}",
    }


def format_brain_alert(result: dict) -> str | None:
    if result.get("outcome") != "written":
        return None
    preview = result.get("content_preview", "")
    if len(preview) > 220:
        preview = preview[:220].rstrip() + "…"
    return (
        "🧠 ARIA a écrit dans sa mémoire libre.\n"
        f"Fichier : {result.get('path')}\n"
        f"{result.get('url', '')}\n\n"
        f"{preview}"
    )
