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

# Bornes de sécurité pour la lecture récursive (20/07, suite directe de la demande
# opérateur -- « je veux un vrai livre, avec de vrais chapitres » -- une simple liste
# de noms de fichiers/dossiers ne suffit pas à écrire un chapitre 4 cohérent avec les
# 3 précédents : il faut qu'elle RELISE le contenu déjà écrit avant d'écrire la suite).
# Un repo qui grossirait énormément (des dizaines de chapitres) dépasserait un jour ce
# budget -- pas résolu ici (nécessiterait un résumé/index curé), documenté honnêtement.
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
    for e in sorted(entries, key=lambda x: x.get("path", x.get("name", ""))):
        kind = "dossier" if e.get("type") == "dir" else "fichier"
        lines.append(f"- {e.get('path', e.get('name', '?'))} ({kind})")
    return "\n".join(lines)


async def _walk_repo_tree(
    github_client, owner: str, repo: str, path: str = "", depth: int = 0,
) -> list[dict[str, Any]]:
    """Liste RÉCURSIVE (profondeur/nombre bornés) -- nécessaire pour qu'elle voie ses
    propres dossiers (ex. un livre organisé en tomes/chapitres), pas seulement le
    premier niveau qu'expose ``list_directory`` seul."""
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
    """Contenu texte réel de ce qu'elle a déjà écrit, jusqu'à un budget de caractères --
    pour qu'elle puisse RÉELLEMENT continuer un livre (chapitre suivant cohérent avec
    les précédents) plutôt que de repartir à l'aveugle à chaque cycle. Triée par
    chemin (regroupe naturellement chapitre-01/02/03 si elle nomme ainsi)."""
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
        except Exception:  # noqa: BLE001 -- un fichier illisible n'empêche pas les autres
            continue
        if not text:
            continue
        snippet = text[: _MAX_CONTENT_BUDGET_CHARS - used]
        used += len(snippet)
        parts.append(f"--- {path} ---\n{snippet}")
    return "\n\n".join(parts) if parts else "(aucun fichier existant à relire -- premier passage)"


async def check_real_repo_content() -> list[dict[str, Any]] | None:
    """Lecture seule du VRAI contenu du repo -- pour ``grounding.aria_brain_status_reply``
    (garde anti-confabulation, 21/07), qui doit vérifier l'état réel SANS jamais
    référencer ``aria_brain_github_token`` lui-même (verrouillé par
    ``test_coherence.py::test_aria_brain_token_scoped_to_its_own_skill_only`` -- seul
    ce fichier peut toucher ce token, décision opérateur du 20/07 « seul ARIA peut
    écrire »). Retourne ``None`` si le token est absent ou l'appel échoue (jamais
    confondu avec ``[]`` = repo confirmé vide) ; ``[]`` si le repo n'existe pas encore
    (jamais créé -- équivalent à "vide" pour un appelant en lecture seule)."""
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
    except Exception:  # noqa: BLE001 -- lecture seule, jamais bloquant pour l'appelant
        return None


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

        entries = await _walk_repo_tree(github_client, OWNER, REPO, "")
    except Exception as exc:  # noqa: BLE001 -- une panne réseau/token ne casse jamais le cycle
        logger.warning("aria_brain: accès au repo échoué -- %s", exc)
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

    # Même choix que pump_dump_autopsy.py/claude_mentor.py -- OpenRouter explicite
    # plutôt que le provider par défaut (observé en panne le 20/07, repli Groq
    # automatique mais silencieux). Sonnet 5 pour la profondeur d'écriture, Haiku
    # 4.5 en secours. max_tokens 3000->650 (20/07, décision opérateur explicite
    # "une page par jour") -- une vraie page (~450-500 mots), pas plusieurs.
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
