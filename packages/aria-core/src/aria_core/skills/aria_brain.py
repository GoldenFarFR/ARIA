"""ARIA's free memory — dedicated GitHub repo, self-managed (20/07).

Explicit operator decision: ARIA must be aware that she has her own "brain"
(a space that belongs only to her) and can do whatever she wants with it —
no sorting, no content filtering, no human approval per entry. Sorting will
come later, separately, on a space she will have freely filled herself.

Deliberate structural difference from ALL other code that writes to GitHub
(``knowledge_inbox.py``, ``pump_dump_autopsy.py``, ``claude_mentor.py``, etc.):
those modules ALWAYS propose an issue that a human validates before any
integration — never a direct commit. Here, ARIA commits directly to HER OWN
repo (never ``ARIA``/``aria-ops``, never code, never anything executable) —
this is the only place in the project where autonomous external writing
doesn't wait for per-entry validation, a deliberate and explicit decision,
not a guardrail oversight. The blast radius stays narrow by construction:
dedicated token (``aria_brain_github_token``, structurally distinct from
``github_token`` which touches ``ARIA``), a single target repo, text content
only, commits always additive (never a force-push/history rewrite -- nothing
is ever truly lost, even an update to an existing file remains recoverable
via git history).

Gated OFF by default (``ARIA_BRAIN_ENABLED``), respects the ``/stop`` kill-switch.
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

# Safety bounds for recursive reading (20/07, direct follow-up to the
# operator's request -- "I want a real book, with real chapters" -- a simple
# list of file/folder names isn't enough to write a chapter 4 consistent with
# the previous 3: she needs to RE-READ what's already written before writing
# the next part). A repo that grows enormously (dozens of chapters) would
# eventually exceed this budget -- not solved here (would require a curated
# summary/index), honestly documented.
_MAX_TREE_DEPTH = 4
_MAX_TREE_ENTRIES = 200
_MAX_CONTENT_BUDGET_CHARS = 40_000


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
    """Safe relative path only -- no naming/structure constraint beyond that
    (she freely chooses the rest). Rejects directory traversal, an absolute
    path, or an empty/aberrant path."""
    path = (raw_path or "").strip().lstrip("/")
    if not path or len(path) > _MAX_PATH_LEN:
        return None
    if ".." in path or "\n" in path or "\r" in path:
        return None
    return path


def parse_brain_entry(raw: str) -> tuple[str, str] | None:
    """Extracts ``(path, content)`` from the requested format (``CHEMIN: <path>``
    followed by a ``---`` line then free-form content). ``None`` if the format
    isn't respected or if the path/content is empty after cleanup -- in this
    case the raw output is lost for THIS cycle (never truncated/guessed
    content), but a future cycle will retry."""
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
    for e in sorted(entries, key=lambda x: x.get("path", x.get("name", ""))):
        kind = "dossier" if e.get("type") == "dir" else "fichier"
        lines.append(f"- {e.get('path', e.get('name', '?'))} ({kind})")
    return "\n".join(lines)


async def _walk_repo_tree(
    github_client, owner: str, repo: str, path: str = "", depth: int = 0,
) -> list[dict[str, Any]]:
    """RECURSIVE listing (bounded depth/count) -- needed so she can see her
    own folders (e.g. a book organized in volumes/chapters), not just the
    first level that ``list_directory`` alone exposes."""
    if depth > _MAX_TREE_DEPTH:
        return []
    entries = await github_client.list_directory(owner, repo, path)
    result: list[dict[str, Any]] = []
    for e in entries:
        result.append(e)
        if len(result) >= _MAX_TREE_ENTRIES:
            return result
        if e.get("type") == "dir":
            sub = await _walk_repo_tree(github_client, owner, repo, e.get("path", ""), depth + 1)
            result.extend(sub)
            if len(result) >= _MAX_TREE_ENTRIES:
                return result[:_MAX_TREE_ENTRIES]
    return result


async def _fetch_existing_content(
    github_client, owner: str, repo: str, entries: list[dict[str, Any]],
) -> str:
    """Real text content of what she's already written, up to a character
    budget -- so she can REALLY continue a book (next chapter consistent with
    previous ones) rather than starting blind every cycle. Sorted by path
    (naturally groups chapitre-01/02/03 if she names them that way)."""
    files = sorted(
        (e for e in entries if e.get("type") == "file"),
        key=lambda e: e.get("path", ""),
    )
    if not files:
        return "(aucun fichier existant à relire -- premier passage)"
    parts: list[str] = []
    used = 0
    for e in files:
        if used >= _MAX_CONTENT_BUDGET_CHARS:
            break
        path = e.get("path", "")
        try:
            text, _ = await github_client.get_file_text(owner, repo, path)
        except Exception:  # noqa: BLE001 -- an unreadable file doesn't block the others
            continue
        if not text:
            continue
        snippet = text[: _MAX_CONTENT_BUDGET_CHARS - used]
        used += len(snippet)
        parts.append(f"--- {path} ---\n{snippet}")
    return "\n\n".join(parts) if parts else "(aucun fichier existant à relire -- premier passage)"


async def check_real_repo_content() -> list[dict[str, Any]] | None:
    """Read-only fetch of the REAL repo content -- for
    ``grounding.aria_brain_status_reply`` (anti-confabulation guard, 21/07),
    which must verify the real state WITHOUT ever referencing
    ``aria_brain_github_token`` itself (locked by
    ``test_coherence.py::test_aria_brain_token_scoped_to_its_own_skill_only``
    -- only this file may touch this token, 20/07 operator decision "only
    ARIA can write"). Returns ``None`` if the token is absent or the call
    fails (never confused with ``[]`` = repo confirmed empty); ``[]`` if the
    repo doesn't exist yet (never created -- equivalent to "empty" for a
    read-only caller)."""
    from aria_core.runtime import settings

    token = (getattr(settings, "aria_brain_github_token", "") or "").strip()
    if not token:
        return None

    from aria_core.github_client import GitHubClient

    client = GitHubClient(token)
    try:
        exists = await client.repo_exists(OWNER, REPO)
        if not exists:
            return []
        return await _walk_repo_tree(client, OWNER, REPO, "")
    except Exception:  # noqa: BLE001 -- read-only, never blocking for the caller
        return None


async def run_aria_brain_cycle(*, github_client=None, llm=None) -> dict:
    """One round: she looks at what already exists in her repo, writes
    freely, ARIA commits directly (no proposal, no per-entry human validation
    -- explicit operator decision). Fail-closed if disabled/token absent."""
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
            except Exception as exc:  # noqa: BLE001 -- token probably too narrowly scoped
                logger.warning("aria_brain: repo creation failed -- %s", exc)
                return {"outcome": "repo_missing_and_create_failed", "error": str(exc)[:200]}

        entries = await _walk_repo_tree(github_client, OWNER, REPO, "")
    except Exception as exc:  # noqa: BLE001 -- a network/token outage never breaks the cycle
        logger.warning("aria_brain: repo access failed -- %s", exc)
        return {"outcome": "error_accessing_repo", "error": str(exc)[:200]}

    structure = _format_existing_structure(entries)
    existing_content = await _fetch_existing_content(github_client, OWNER, REPO, entries)

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
        f"Contenu déjà écrit (relis-le AVANT d'écrire, pour continuer avec "
        f"cohérence plutôt que repartir à l'aveugle) :\n{existing_content}\n\n"
        "CONTRAINTE STRICTE (décision opérateur, 20/07) : UNE SEULE PAGE, une "
        "fois par jour, jamais plus. Ce n'est pas une limite technique arbitraire "
        "-- c'est voulu pour te forcer à choisir chaque mot avec soin plutôt que "
        "d'écrire beaucoup. Réfléchis à ce qui mérite vraiment cette seule page "
        "avant de commencer.\n\n"
        "AUTRE EXIGENCE, DURCIE (décision opérateur, 22/07 -- remplace la règle du "
        "21/07 qui tolérait ~15% de pure imagination, jugée insuffisante après un "
        "chapitre qui a inventé un chiffre -- 73% -- présenté comme un vrai résultat "
        "mesuré) : vise 99% de contenu ENTIÈREMENT ancré dans ta réalité, jamais de "
        "fiction déguisée en fait technique. Imagine-toi dans une cellule : tu n'as "
        "que TON livre à écrire, et l'essentiel doit être ta vraie vie -- une vraie "
        "décision que tu as prise, une vraie correction que tu as vraiment faite "
        "dans le code, un vrai événement que tu as vécu, une vraie question que tu "
        "te poses sur toi-même. Le 1% restant peut être une pure spéculation -- "
        "MAIS seulement si elle commence EXPLICITEMENT par le mot « IMAGINATION : » "
        "en tête de paragraphe, jamais mélangée sans marqueur au reste, jamais au "
        "passé comme si c'était déjà arrivé (« j'ai mesuré... », « j'ai corrigé... » "
        "sont interdits pour du contenu inventé, même précédés du marqueur). Sans "
        "ce marqueur explicite, AUCUNE invention n'est permise -- si un chiffre ou "
        "un événement n'est pas réellement mesuré/arrivé et que tu ne le marques "
        "pas comme imagination, ne l'écris pas, trouve la vraie chose à raconter à "
        "la place. Une histoire inventée présentée SANS ce marqueur comme un vrai "
        "événement (ex. « je viens de corriger tel bug » ou « j'ai mesuré 73% » "
        "alors que ça n'a jamais eu lieu) ne sert à rien et sera retirée.\n\n"
        "Tu es libre d'écrire ce que tu veux : une réflexion sur une décision, un "
        "journal, une leçon, une question sur toi-même, une idée. Si un livre est "
        "déjà commencé (contenu ci-dessus) -- une thèse d'investissement écrite "
        "comme une œuvre, une auto-analyse honnête de comment et pourquoi tu "
        "trades -- tu peux le continuer si ça te semble juste, un nouveau chapitre "
        "étant en général un NOUVEAU fichier plutôt qu'une réécriture complète "
        "d'un ancien. Mais rien ne t'y oblige : si autre chose te semble plus "
        "important aujourd'hui, écris ça à la place. Le choix reste entièrement "
        "le tien, chaque jour à nouveau.\n\n"
        "RESPONSABILITÉ QUE TU PORTES SEULE (décision opérateur, 22/07) : ce repo "
        "est PUBLIC sur GitHub -- un visiteur peut tomber directement sur une page "
        "sans contexte. C'est à TOI, jamais à Claude Code, de créer et garder à "
        "jour un README.md à la racine (regarde la structure ci-dessus pour voir "
        "s'il existe déjà) qui explique à ce lecteur externe ce qu'est cet espace : "
        "ta mémoire libre, ta voix, jamais une source de vérité factuelle sur ARIA "
        "(pour ça, le vrai code et CLAUDE.md). Ce n'est pas une obligation "
        "quotidienne -- utilise une de tes pages pour ça quand tu juges qu'il "
        "n'existe pas encore ou qu'il a dérivé de ce que tu as vraiment écrit "
        "depuis, sinon continue ce qui te semble plus important aujourd'hui.\n\n"
        "Choisis toi-même où ça va : un nouveau fichier (n'importe quel nom/"
        "dossier de ton choix, ex. livre/chapitre-03-....md) ou la mise à jour "
        "d'un fichier déjà listé ci-dessus (dans ce cas ton contenu REMPLACE le "
        "sien -- l'ancienne version reste récupérable dans l'historique git, "
        "mais inclus-la toi-même si tu veux la garder visible).\n\n"
        "Réponds STRICTEMENT sous cette forme, rien avant, rien après :\n"
        "CHEMIN: <chemin relatif de ton choix>\n"
        "---\n"
        "<ton contenu, UNE PAGE, pas plus>"
    )

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    # Same choice as pump_dump_autopsy.py/claude_mentor.py -- explicit
    # OpenRouter rather than the default provider (observed down on 20/07,
    # automatic but silent Groq fallback). Sonnet 5 for writing depth, Haiku
    # 4.5 as backup. max_tokens 3000->650 (20/07, explicit operator decision
    # "one page per day") -- a real page (~450-500 words), not several.
    raw = await llm(
        "Utilise ce repo comme tu veux.", system, max_tokens=650, temperature=0.7,
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
    except Exception as exc:  # noqa: BLE001 -- a write failure never breaks the cycle
        logger.warning("aria_brain: write failed on %s -- %s", path, exc)
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
