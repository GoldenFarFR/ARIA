"""Boîte de dépôt de connaissance -- ARIA lit les notes déposées dans
`docs/aria-learning-inbox/` et PROPOSE (jamais n'impose) comment les intégrer à sa vraie
base de connaissance (`knowledge/*.yaml`, `truth_ledger/canonical_facts.yaml`). Même
doctrine que `code_proposal.py` : ISSUE GitHub uniquement, jamais un commit ni une fusion
autonome -- ce sont des fichiers de VÉRITÉ qu'elle répète ensuite en conversation, une
intégration mal filtrée est plus dangereuse qu'un bug de code ordinaire.

Gaté OFF par défaut (`ARIA_KNOWLEDGE_INBOX_ENABLED`) -- action visible sur le repo public,
même politique que `code_proposal_cycle`/`showcase_pr_watch` pour toute action GitHub
autonome. Chaque note n'est proposée qu'UNE fois (mémorisé localement, jamais retraité).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

INBOX_PATH = "docs/aria-learning-inbox"
TARGET_REPO = "ARIA"

# Trouvé en conditions réelles (17/07, issue #31) : une note écrite le 13/07 décrivant un
# gap dans vanguard/deploy.sh a été transformée en proposition d'issue GitHub le 17/07 --
# SANS jamais revérifier que le gap existait encore, alors qu'il avait été corrigé le jour
# même de l'écriture de la note (la file d'attente traite une note par cycle, dans l'ordre
# de la liste GitHub, pas par fraîcheur -- un simple backlog suffit à créer ce décalage).
# `_REFERENCED_PATH_RE`/`_current_file_states` réinjectent l'état RÉEL des fichiers cités
# entre backticks dans la note (ex. `` `vanguard/deploy.sh` ``) pour que le LLM puisse
# juger si la note est encore d'actualité avant de la publier, au lieu de lui faire
# confiance aveuglément sur son seul contenu figé.
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
    """Chemins entre backticks ressemblant à un fichier réel (ex. `` `vanguard/deploy.sh` ``),
    dans l'ordre d'apparition, sans doublon -- simple heuristique, pas un parseur de repo."""
    seen: list[str] = []
    for match in _REFERENCED_PATH_RE.finditer(content or ""):
        path = match.group(1)
        if path not in seen:
            seen.append(path)
    return seen


async def _current_file_states(github_client, owner: str, paths: list[str]) -> str:
    """Récupère le contenu ACTUEL (tronqué) des chemins cités par la note, au mieux --
    un chemin introuvable/mal formé (ex. relatif, hors racine) est silencieusement ignoré,
    jamais une erreur qui bloque le cycle. Chaîne vide si rien de récupérable."""
    sections: list[str] = []
    for path in paths[:_MAX_REFERENCED_FILES]:
        try:
            text, _sha = await github_client.get_file_text(owner, TARGET_REPO, path)
        except Exception:  # noqa: BLE001 -- un chemin cité qui ne resout a rien de reel, jamais fatal
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
    """Réclame ``path`` atomiquement -- ``True`` seulement si CETTE tentative a
    réellement créé la ligne (jamais laisser deux passages concurrents traiter
    la même note et produire deux issues différentes).

    20/07 -- bug réel trouvé en conditions réelles : issues #42/#43 créées à 6
    minutes d'écart à partir de la MÊME note (Note du 2026-07-15, Clanker/
    GoPlus), avec des propositions différentes (fichiers cibles différents,
    formulation différente) -- deux passages ont vu ``_already_processed() ->
    False`` avant que l'un des deux n'écrive (l'ancien `_mark_processed`
    n'arrivait qu'APRÈS tout le travail LLM, laissant une large fenêtre de
    course). Corrigé : la réclamation se fait ICI, avant tout appel LLM --
    ``_mark_processed``/l'ancien flux en aval sont retirés, cette fonction est
    l'unique point d'écriture désormais."""
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
    """Un tour : repère UNE note non traitée, propose son intégration comme ISSUE GitHub
    (jamais un commit, jamais une fusion). Fail-closed à chaque étage."""
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
    except Exception as exc:  # noqa: BLE001 -- un dossier absent/erreur reseau ne casse jamais le heartbeat
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

    # 20/07 -- réclamation ATOMIQUE ici, avant tout travail (fetch/LLM) -- voir le
    # commentaire de _try_claim pour l'incident réel (issues #42/#43 dupliquées)
    # que ce réordonnancement corrige. Si un autre passage a déjà réclamé cette
    # note (concurrence), on s'arrête immédiatement -- jamais un deuxième appel LLM
    # ni une deuxième issue pour la même note.
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
