"""ARIA → Ouvrier Cursor — file d'attente quand ARIA est bloquée.

Quand ARIA ne peut pas implémenter elle-même, elle écrit dans
`collegue-memoire/sessions/ARIA-WORKER.md` (SSOT GitHub).
Cursor/Grok lit ce fichier à chaque session et traite les items `[pending]`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

WORKER_REPO = "collegue-memoire"
WORKER_PATH = "sessions/ARIA-WORKER.md"
FALLBACK_REPO = "aria-sandbox"
FALLBACK_PATH = "docs/aria-worker-queue/QUEUE.md"

PENDING_TAG = "[pending]"
DONE_TAG = "[done]"


@dataclass
class WorkerTask:
    task_id: str
    title: str
    source: str
    problem: str
    action: str
    priority: str = "normal"
    repos: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    issue_url: str = ""
    pr_url: str = ""
    context: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    def to_markdown(self) -> str:
        repos = ", ".join(self.repos) if self.repos else "—"
        files = ", ".join(self.files) if self.files else "—"
        lines = [
            f"## {PENDING_TAG} {self.task_id} — {self.created_at}",
            "",
            f"**Titre :** {self.title}  ",
            f"**Source :** `{self.source}` · **Priorité :** {self.priority}  ",
            f"**Repo(s) :** {repos}  ",
            f"**Fichiers :** {files}  ",
            "",
            "### Problème",
            self.problem.strip() or "(non précisé)",
            "",
            "### Action demandée à l'ouvrier Cursor",
            self.action.strip() or "Implémenter et valider (tests + deploy si besoin).",
            "",
        ]
        if self.acceptance:
            lines.append("### Critères d'acceptation")
            lines.extend(f"- [ ] {a}" for a in self.acceptance)
            lines.append("")
        if self.context.strip():
            lines.extend(["### Contexte", "```", self.context.strip()[:3000], "```", ""])
        links: list[str] = []
        if self.issue_url:
            links.append(f"- Issue : {self.issue_url}")
        if self.pr_url:
            links.append(f"- PR : {self.pr_url}")
        if links:
            lines.append("### Liens")
            lines.extend(links)
            lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)


def _queue_dir() -> Path:
    from aria_core.paths import data_dir

    path = data_dir() / "aria-worker-queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local_jsonl() -> Path:
    return _queue_dir() / "tasks.jsonl"


def _worker_header() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "# ARIA → Ouvrier Cursor\n\n"
        "> **SSOT** — ARIA écrit ici quand elle est bloquée.  \n"
        "> **Cursor/Grok (ouvrier)** : à chaque session, traiter **tous** les items `[pending]`, "
        "puis les passer en `[done]`.\n\n"
        f"Dernière mise à jour ARIA : {ts}\n\n"
        "---\n\n"
    )


def _has_pending_task(content: str, task_id: str) -> bool:
    pattern = re.compile(
        rf"^##\s+\[pending\]\s+{re.escape(task_id)}\b",
        re.MULTILINE | re.IGNORECASE,
    )
    return bool(pattern.search(content))


def _append_task_to_markdown(existing: str, task: WorkerTask) -> str:
    body = existing.strip()
    if not body:
        body = _worker_header() + "_Aucun item pour l'instant._\n\n---\n\n"
    elif "ARIA → Ouvrier" not in body:
        body = _worker_header() + body + "\n\n---\n\n"
    if _has_pending_task(body, task.task_id):
        return body
    if "_Aucun item pour l'instant._" in body:
        body = body.replace("_Aucun item pour l'instant._\n\n---\n\n", "")
    return body.rstrip() + "\n\n" + task.to_markdown()


def mark_task_done_in_markdown(content: str, task_id: str) -> str:
    pattern = re.compile(
        rf"^##\s+\[pending\]\s+({re.escape(task_id)})\b",
        re.MULTILINE | re.IGNORECASE,
    )
    return pattern.sub(rf"## {DONE_TAG} \1", content, count=1)


def count_pending_tasks(content: str) -> int:
    return len(re.findall(r"^##\s+\[pending\]\s+", content, re.MULTILINE | re.IGNORECASE))


def _append_local_record(task: WorkerTask, *, status: str, extra: dict[str, Any] | None = None) -> None:
    record = {
        "task_id": task.task_id,
        "title": task.title,
        "source": task.source,
        "status": status,
        "created_at": task.created_at,
        **(extra or {}),
    }
    with _local_jsonl().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _push_worker_markdown(
    owner: str,
    repo: str,
    path: str,
    new_content: str,
    *,
    message: str,
) -> dict[str, Any]:
    from aria_core.github_client import GitHubClient

    client = GitHubClient(settings.github_token.strip())
    _text, sha = await client.get_file_text(owner, repo, path)
    result = await client.put_file(
        owner,
        repo,
        path,
        new_content,
        message=message,
        sha=sha or None,
    )
    commit_sha = ""
    if isinstance(result.get("commit"), dict):
        commit_sha = result["commit"].get("sha", "")
    return {"repo": f"{owner}/{repo}", "path": path, "commit_sha": commit_sha}


async def enqueue_worker_task(
    *,
    task_id: str,
    title: str,
    source: str,
    problem: str,
    action: str,
    priority: str = "normal",
    repos: tuple[str, ...] | list[str] = (),
    files: tuple[str, ...] | list[str] = (),
    acceptance: tuple[str, ...] | list[str] = (),
    issue_url: str = "",
    pr_url: str = "",
    context: str = "",
    lang: str = "fr",
) -> dict[str, Any]:
    """
    ARIA bloquée → file ouvrier Cursor.
    Pousse sur collegue-memoire/sessions/ARIA-WORKER.md (fallback aria-sandbox).
    """
    from aria_core.skills.github_skill import github_configured, repo_write_allowed

    clean_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", task_id.strip())[:80].strip("-") or "task"
    task = WorkerTask(
        task_id=clean_id,
        title=title.strip()[:200],
        source=source.strip()[:80],
        problem=problem.strip(),
        action=action.strip(),
        priority=priority if priority in ("low", "normal", "high", "critical") else "normal",
        repos=tuple(repos),
        files=tuple(files),
        acceptance=tuple(acceptance),
        issue_url=issue_url.strip(),
        pr_url=pr_url.strip(),
        context=context.strip(),
    )

    result: dict[str, Any] = {
        "task_id": task.task_id,
        "title": task.title,
        "status": "queued_local",
        "worker_path": WORKER_PATH,
        "local_jsonl": str(_local_jsonl()),
    }

    _append_local_record(task, status="pending")

    if not github_configured():
        append_memory("self-improve", f"[aria-worker] {task.task_id} local only (no GITHUB_TOKEN)")
        result["status"] = "local_only"
        await _notify_worker_task(task, result, lang=lang)
        return result

    owner = settings.github_owner.strip()
    targets: list[tuple[str, str]] = []
    if repo_write_allowed(owner, WORKER_REPO):
        targets.append((WORKER_REPO, WORKER_PATH))
    if repo_write_allowed(owner, FALLBACK_REPO):
        targets.append((FALLBACK_REPO, FALLBACK_PATH))

    if not targets:
        append_memory("self-improve", f"[aria-worker] {task.task_id} write denied")
        result["status"] = "write_denied"
        await _notify_worker_task(task, result, lang=lang)
        return result

    pushed: list[dict[str, Any]] = []
    errors: list[str] = []
    for repo, path in targets:
        try:
            text, _ = await _read_github_file(owner, repo, path)
            if _has_pending_task(text, task.task_id):
                result["status"] = "dedup"
                result["dedup_repo"] = f"{owner}/{repo}"
                append_memory("self-improve", f"[aria-worker] dedup {task.task_id}")
                await _notify_worker_task(task, result, lang=lang)
                return result
            new_md = _append_task_to_markdown(text, task)
            push = await _push_worker_markdown(
                owner,
                repo,
                path,
                new_md,
                message=f"aria-worker: {task.task_id} — {task.title[:60]}",
            )
            pushed.append(push)
        except Exception as exc:
            logger.warning("aria_worker_queue push %s/%s failed: %s", owner, repo, exc)
            errors.append(f"{repo}: {exc}"[:200])

    if pushed:
        result["status"] = "pushed"
        result["pushed"] = pushed
        append_memory(
            "self-improve",
            f"[aria-worker] {task.task_id} pushed → {pushed[0].get('repo')}:{pushed[0].get('path')}",
        )
    else:
        result["status"] = "push_failed"
        result["errors"] = errors

    await _notify_worker_task(task, result, lang=lang)
    return result


async def _read_github_file(owner: str, repo: str, path: str) -> tuple[str, str]:
    from aria_core.github_client import GitHubClient

    client = GitHubClient(settings.github_token.strip())
    return await client.get_file_text(owner, repo, path)


async def enqueue_from_capability_gap(
    capability_id: str,
    *,
    context: str = "",
    gap_result: dict[str, Any] | None = None,
    lang: str = "fr",
) -> dict[str, Any]:
    """File ouvrier à partir d'un capability gap."""
    from aria_core.capability_gap import CAPABILITY_SPECS

    spec = CAPABILITY_SPECS.get(capability_id, {})
    files = tuple(spec.get("target_files") or ())
    acceptance = tuple(spec.get("acceptance") or ())
    repo = spec.get("target_repo") or settings.github_sandbox_repo
    gr = gap_result or {}
    return await enqueue_worker_task(
        task_id=f"cap-gap-{capability_id}",
        title=spec.get("title") or f"Lacune : {capability_id}",
        source="capability_gap",
        problem=context.strip() or f"Capacité `{capability_id}` indisponible ou bloquée.",
        action=(
            "Implémenter la capacité dans aria-core (ou config opérateur), "
            "tests, bump pin aria-vanguard si besoin, sync-render + preuve health."
        ),
        priority="high" if capability_id.startswith(("security_", "health_", "operator_")) else "normal",
        repos=(repo,),
        files=files,
        acceptance=acceptance,
        issue_url=str(gr.get("issue_url") or ""),
        pr_url=str(gr.get("pr_url") or ""),
        context=context,
        lang=lang,
    )


async def enqueue_from_gem_crush_block(
    *,
    status: str,
    version: int | None = None,
    message: str = "",
    title: str = "",
    lang: str = "fr",
) -> dict[str, Any]:
    """File ouvrier quand Gem Crush ne peut pas shipper."""
    ver = version or 0
    action_map = {
        "error": "Corriger les ancres de patch ou le code source, puis laisser ARIA re-ship.",
        "write_denied": "Ajouter aria-vanguard dans GITHUB_WRITE_REPOS sur Render, redeploy.",
        "quality_gate": "Enrichir la release en file (≥10 items + patch TS) dans gem_crush_premium.py.",
        "queue_empty": "Vérifier aria_gem_crush_unlimited_releases + gem_crush_synthesizer (exploration ouverte).",
        "missing": "Pousser le POC Gem Crush sur aria-vanguard (fichiers manquants sur GitHub).",
    }
    return await enqueue_worker_task(
        task_id=f"gem-crush-{status}-v{ver}" if ver else f"gem-crush-{status}",
        title=title or f"Gem Crush bloqué — {status}",
        source="gem_crush_skill",
        problem=message.strip() or f"Heartbeat gem_crush_daily status={status}",
        action=action_map.get(status, "Diagnostiquer et débloquer le pipeline Gem Crush."),
        priority="high" if status in ("error", "write_denied") else "normal",
        repos=("aria-vanguard", "aria-sandbox"),
        files=(
            "src/games/aria-gem-crush/components/GemCrushGame.tsx",
            "packages/aria-core/src/aria_core/skills/gem_crush_premium.py",
        ),
        acceptance=(
            "Prochain heartbeat gem_crush_daily → status=applied",
            "Visible sur ariavanguardzhc.com #poc après redeploy",
        ),
        context=message[:2000],
        lang=lang,
    )


async def _notify_worker_task(task: WorkerTask, result: dict[str, Any], *, lang: str) -> None:
    try:
        from aria_core.gateway.telegram_bot import notify_admin
    except ImportError:
        return

    if lang == "fr":
        msg = (
            "ARIA → Ouvrier Cursor\n\n"
            f"Tâche : {task.task_id}\n"
            f"{task.title}\n\n"
            f"Statut file : {result.get('status')}\n"
        )
    else:
        msg = f"ARIA worker queue — {task.task_id}\n{result.get('status')}\n"

    pushed = result.get("pushed") or []
    if pushed:
        p = pushed[0]
        msg += f"File : {p.get('repo')}/{p.get('path')}\n"
    elif result.get("status") == "local_only":
        msg += f"Local : {result.get('local_jsonl')}\n"
        msg += "Ouvrier : lire sessions/ARIA-WORKER.md après pull collegue-memoire.\n"

    if result.get("status") == "dedup":
        msg += "(déjà en file cette semaine)\n"

    await notify_admin(msg.strip())


def format_worker_reply(result: dict[str, Any], *, lang: str = "fr") -> str:
    if lang == "fr":
        lines = ["J'ai ajouté une tâche pour l'ouvrier Cursor :"]
    else:
        lines = ["Queued task for Cursor worker:"]
    lines.append(f"ID : {result.get('task_id')}")
    if result.get("status") == "pushed":
        pushed = (result.get("pushed") or [{}])[0]
        lines.append(f"File : {pushed.get('repo')}/{pushed.get('path')}")
    if lang == "fr":
        lines.append("L'ouvrier traitera `sessions/ARIA-WORKER.md` à la prochaine session.")
    else:
        lines.append("Worker will process sessions/ARIA-WORKER.md next session.")
    return "\n".join(lines)