"""Knowledge drop box -- ARIA reads the notes dropped into
`docs/aria-learning-inbox/` and PROPOSES (never imposes) how to integrate them into her
real knowledge base (`knowledge/*.yaml`, `truth_ledger/canonical_facts.yaml`). Same
doctrine as `code_proposal.py`: GitHub ISSUE only, never an autonomous commit or merge
-- these are TRUTH files she then repeats in conversation, a poorly filtered
integration is more dangerous than an ordinary code bug.

Gated OFF by default (`ARIA_KNOWLEDGE_INBOX_ENABLED`) -- visible action on the public
repo, same policy as `code_proposal_cycle`/`showcase_pr_watch` for any autonomous
GitHub action. Each note is only ever proposed ONCE (remembered locally, never
reprocessed).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

INBOX_PATH = "docs/aria-learning-inbox"
TARGET_REPO = "ARIA"

# Found in real conditions (17/07, issue #31): a note written on 13/07 describing a
# gap in vanguard/deploy.sh was turned into a GitHub issue proposal on 17/07 --
# WITHOUT ever re-checking that the gap still existed, even though it had been fixed
# the very day the note was written (the queue processes one note per cycle, in the
# order of the GitHub listing, not by freshness -- a simple backlog is enough to
# create this drift). `_REFERENCED_PATH_RE`/`_current_file_states` re-inject the REAL
# state of the files cited between backticks in the note (e.g. `` `vanguard/deploy.sh` ``)
# so the LLM can judge whether the note is still current before publishing it, instead
# of blindly trusting its own frozen content.
_REFERENCED_PATH_RE = re.compile(r"`([A-Za-z0-9_\-./]+\.[A-Za-z0-9]+)`")
_MAX_REFERENCED_FILES = 3
_REFERENCED_FILE_CHARS = 2000

_PROPOSAL_SYSTEM = (
    "Tu es ARIA. On te montre une note brute déposée par l'opérateur, destinée à enrichir "
    "ta connaissance, ET l'état ACTUEL des fichiers qu'elle cite (si trouvés). Ta tâche : "
    "proposer PRÉCISÉMENT comment l'intégrer dans tes vrais fichiers de connaissance "
    "(knowledge/*.yaml pour les règles/méthodologie, truth_ledger/canonical_facts.yaml "
    "pour les faits établis) -- JAMAIS dans CLAUDE.md (ce fichier ne te concerne pas, il "
    "brief Claude Code, pas toi). Rédige une proposition d'issue GitHub structurée : "
    "Résumé de la note, Fichier(s) cible(s) précis, Contenu proposé (extrait exact à "
    "ajouter), Risques (contradiction avec un fait existant ?). Si la note ne contient "
    "rien d'assez concret/vérifiable pour devenir une connaissance durable, dis-le "
    "clairement (actionable=false) plutôt que d'inventer une proposition creuse. "
    "IMPORTANT -- fraîcheur : si l'état actuel des fichiers montré ci-dessous contredit "
    "ou a déjà résolu ce que décrit la note (ex. la note signale une absence, mais le "
    "code actuel le contient déjà), la note est OBSOLÈTE -- réponds actionable=false et "
    "explique brièvement pourquoi dans 'body' plutôt que de proposer une connaissance "
    "périmée comme si elle était neuve."
)


def _extract_referenced_paths(content: str) -> list[str]:
    """Paths between backticks that look like a real file (e.g. `` `vanguard/deploy.sh` ``),
    in order of appearance, deduplicated -- a simple heuristic, not a repo parser."""
    seen: list[str] = []
    for match in _REFERENCED_PATH_RE.finditer(content or ""):
        path = match.group(1)
        if path not in seen:
            seen.append(path)
    return seen


async def _current_file_states(github_client, owner: str, paths: list[str]) -> str:
    """Fetches the CURRENT (truncated) content of the paths cited by the note, best-effort
    -- an unfindable/malformed path (e.g. relative, outside root) is silently ignored,
    never an error that blocks the cycle. Empty string if nothing is retrievable."""
    sections: list[str] = []
    for path in paths[:_MAX_REFERENCED_FILES]:
        try:
            text, _sha = await github_client.get_file_text(owner, TARGET_REPO, path)
        except Exception:  # noqa: BLE001 -- a cited path resolving to nothing real is never fatal
            continue
        if not (text or "").strip():
            continue
        sections.append(f"--- {path} (état actuel) ---\n{text[:_REFERENCED_FILE_CHARS]}")
    return "\n\n".join(sections)


def knowledge_inbox_enabled() -> bool:
    from aria_core.skills.github_skill import github_configured

    if not github_configured():
        return False
    return os.environ.get("ARIA_KNOWLEDGE_INBOX_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_processed_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_inbox_processed ("
        "path TEXT PRIMARY KEY, processed_at TEXT NOT NULL)"
    )
    await db.commit()


async def _already_processed(path: str) -> bool:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_processed_table(db)
        cursor = await db.execute(
            "SELECT 1 FROM knowledge_inbox_processed WHERE path = ?", (path,)
        )
        row = await cursor.fetchone()
    return row is not None


async def _mark_processed(path: str) -> None:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_processed_table(db)
        await db.execute(
            "INSERT OR IGNORE INTO knowledge_inbox_processed (path, processed_at) VALUES (?, ?)",
            (path, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _try_claim(path: str) -> bool:
    """Atomically claims ``path`` -- ``True`` only if THIS attempt actually
    created the row (never let two concurrent passes process the same note
    and produce two different issues).

    20/07 -- real bug found in live conditions: issues #42/#43 created 6
    minutes apart from the SAME note (Note from 2026-07-15, Clanker/
    GoPlus), with different proposals (different target files, different
    wording) -- two passes saw ``_already_processed() ->
    False`` before either one wrote (the old `_mark_processed`
    only ran AFTER all the LLM work, leaving a wide race window).
    Fixed: the claim now happens HERE, before any LLM call --
    ``_mark_processed``/the old downstream flow are removed, this function is
    now the sole write point."""
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_processed_table(db)
        cur = await db.execute(
            "INSERT OR IGNORE INTO knowledge_inbox_processed (path, processed_at) VALUES (?, ?)",
            (path, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cur.rowcount > 0


def _pick_next_candidate(entries: list[dict], already_processed: set[str]) -> str | None:
    for entry in entries:
        name = entry.get("name", "")
        if not name or name.startswith(".") or name.lower() == "readme.md":
            continue
        if not (name.lower().endswith(".md") or name.lower().endswith(".txt")):
            continue
        path = f"{INBOX_PATH}/{name}"
        if path in already_processed:
            continue
        return path
    return None


async def run_knowledge_inbox_cycle(*, llm=None, github_client=None, notifier=None) -> dict:
    """One pass: spots ONE unprocessed note, proposes its integration as a GitHub ISSUE
    (never a commit, never a merge). Fail-closed at every stage."""
    if not knowledge_inbox_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return {"outcome": "no_token"}
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    try:
        entries = await github_client.list_directory(owner, TARGET_REPO, INBOX_PATH)
    except Exception as exc:  # noqa: BLE001 -- a missing folder/network error never breaks the heartbeat
        return {"outcome": "error", "error": str(exc)[:300]}

    processed_paths = set()
    for entry in entries:
        name = entry.get("name", "")
        path = f"{INBOX_PATH}/{name}"
        if await _already_processed(path):
            processed_paths.add(path)

    path = _pick_next_candidate(entries, processed_paths)
    if path is None:
        return {"outcome": "nothing_new"}

    # 20/07 -- ATOMIC claim here, before any work (fetch/LLM) -- see the
    # comment on _try_claim for the real incident (duplicate issues #42/#43)
    # this reordering fixes. If another pass already claimed this note
    # (concurrency), stop immediately -- never a second LLM call nor a
    # second issue for the same note.
    if not await _try_claim(path):
        return {"outcome": "lost_claim_race", "path": path}

    try:
        content, _sha = await github_client.get_file_text(owner, TARGET_REPO, path)
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "error": str(exc)[:300], "path": path}

    if not content.strip():
        return {"outcome": "empty_skipped", "path": path}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    referenced_paths = _extract_referenced_paths(content)
    current_state = await _current_file_states(github_client, owner, referenced_paths)
    current_state_block = (
        f"\n\nÉtat actuel des fichiers cités par la note (vérifie la fraîcheur avant de "
        f"proposer) :\n\n{current_state}\n"
        if current_state
        else ""
    )

    prompt = (
        f"Note déposée dans {path} :\n\n{content[:4000]}\n"
        f"{current_state_block}\n"
        'Réponds STRICTEMENT en JSON : {"title": "<titre court>", '
        '"body": "<proposition structurée en markdown>", "actionable": true|false}'
    )
    raw = await llm(prompt, _PROPOSAL_SYSTEM, max_tokens=700)
    if not raw:
        return {"outcome": "generation_failed", "path": path}

    try:
        data = json.loads(raw)
        title = str(data.get("title", "")).strip()
        body = str(data.get("body", "")).strip()
        actionable = bool(data.get("actionable", True))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"outcome": "parse_failed", "path": path}

    if not actionable or not title or not body:
        return {"outcome": "not_actionable", "path": path}

    body_full = (
        body
        + f"\n\n---\n*Proposition générée par ARIA à partir de `{path}` -- revue humaine "
        "requise avant toute intégration dans ses fichiers de connaissance. Elle n'écrit "
        "ni ne fusionne jamais rien seule.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[connaissance] {title}", body_full,
            labels=["aria-knowledge-proposal"],
        )
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "error": str(exc)[:300], "path": path}

    url = issue.get("html_url", "")
    if notifier:
        try:
            await notifier(f"🧠 Proposition de connaissance ARIA -- {title}\n{url}")
        except Exception:  # noqa: BLE001
            pass

    return {"outcome": "ok", "title": title, "url": url, "path": path}
