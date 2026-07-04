"""ARIA → Ouvrier Cursor — file d'attente quand ARIA est bloquée.

Quand ARIA ne peut pas implémenter elle-même, elle écrit dans
`collegue-memoire/sessions/ARIA-WORKER.md` (SSOT GitHub).
Cursor/Grok lit ce fichier à chaque session et traite les items `[pending]`.
"""

from __future__ import annotations

import json
import logging
import os
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


def mark_worker_task_done(
    task_id: str,
    *,
    note: str = "",
    score: int | None = None,
    resolver: str = "cursor",
) -> dict[str, Any]:
    """Passe une tâche [pending] → [done] et auto-note le ledger suggestions."""
    clean_id = (task_id or "").strip()
    if not clean_id:
        return {"status": "invalid", "task_id": task_id}

    path = resolve_local_worker_md()
    if path is None or not path.is_file():
        return {"status": "no_worker_md", "task_id": clean_id}

    content = path.read_text(encoding="utf-8")
    if not _has_pending_task(content, clean_id):
        return {"status": "not_pending", "task_id": clean_id}

    meta = {"task_id": clean_id}
    try:
        from aria_core.suggestion_feedback import parse_worker_task_meta

        meta = parse_worker_task_meta(content, clean_id, pending=True)
    except ImportError:
        pass

    new_content = mark_task_done_in_markdown(content, clean_id)
    path.write_text(new_content, encoding="utf-8")

    _append_local_record(
        _task_from_record(
            {
                "task_id": clean_id,
                "title": meta.get("title") or clean_id,
                "source": meta.get("source") or "ouvrier",
                "problem": "",
                "action": "",
                "priority": meta.get("priority") or "normal",
            },
        ),
        status="done",
        extra={"note": note[:300]} if note else None,
    )

    rating: dict[str, Any] = {"rated": []}
    try:
        from aria_core.suggestion_feedback import auto_rate_worker_done

        rating = auto_rate_worker_done(
            meta,
            score=score,
            note=note or f"mark_worker_task_done {clean_id}",
            resolver=resolver,
        )
    except OSError:
        pass

    append_memory("self-improve", f"[aria-worker] {clean_id} done — rated {len(rating.get('rated') or [])}")
    return {
        "status": "done",
        "task_id": clean_id,
        "worker_md": str(path),
        "score": rating.get("score"),
        "rated": rating.get("rated") or [],
    }


def count_pending_tasks(content: str) -> int:
    return len(list_pending_worker_tasks(content))


def list_pending_worker_tasks(content: str) -> list[dict[str, str]]:
    """Extrait id + titre des sections ## [pending] dans ARIA-WORKER.md."""
    tasks: list[dict[str, str]] = []
    for section in re.split(
        r"(?=^##\s+\[pending\]\s+)",
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    ):
        if not re.match(r"^##\s+\[pending\]\s+", section, re.IGNORECASE):
            continue
        header = re.match(
            r"^##\s+\[pending\]\s+([^\s—]+)",
            section,
            re.IGNORECASE,
        )
        task_id = header.group(1).strip() if header else "?"
        title_m = re.search(r"\*\*Titre\s*:\*\*\s*(.+?)(?:\s{2}|\n)", section)
        title = title_m.group(1).strip() if title_m else task_id
        tasks.append({"task_id": task_id, "title": title})
    return tasks


def list_pending_worker_tasks_from_disk() -> list[dict[str, str]]:
    path = resolve_local_worker_md()
    if not path or not path.is_file():
        return []
    try:
        return list_pending_worker_tasks(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def _local_worker_md_candidates() -> list[Path]:
    """SSOT aria-ops avant copie legacy ARIA/collegue-memoire (gitignore transition)."""
    home = Path.home()
    candidates: list[Path] = []
    for env_key in ("ARIA_OPS_ROOT",):
        raw = os.getenv(env_key, "").strip()
        if raw:
            candidates.append(Path(raw) / "collegue-memoire" / "sessions" / "ARIA-WORKER.md")
    candidates.append(
        home / "GitHub-Repos" / "aria-ops" / "collegue-memoire" / "sessions" / "ARIA-WORKER.md",
    )
    for env_key in ("COLLEGUE_MEMOIRE_ROOT",):
        raw = os.getenv(env_key, "").strip()
        if not raw:
            continue
        root = Path(raw)
        candidates.append(root / "collegue-memoire" / "sessions" / "ARIA-WORKER.md")
        candidates.append(root / "sessions" / "ARIA-WORKER.md")
    for env_key in ("ARIA_REPO_ROOT",):
        raw = os.getenv(env_key, "").strip()
        if not raw:
            continue
        root = Path(raw)
        candidates.append(root / "collegue-memoire" / "sessions" / "ARIA-WORKER.md")
    candidates.extend(
        [
            home / "GitHub-Repos" / "ARIA" / "collegue-memoire" / "sessions" / "ARIA-WORKER.md",
            home / "projets" / "collegue-memoire" / "sessions" / "ARIA-WORKER.md",
        ],
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def resolve_local_worker_md() -> Path | None:
    """Chemin local monorepo / clone collegue-memoire pour l'ouvrier Cursor."""
    for path in _local_worker_md_candidates():
        if path.parent.is_dir():
            return path
    return None


def _write_task_to_local_md(task: WorkerTask) -> Path | None:
    path = resolve_local_worker_md()
    if path is None:
        return None
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if _has_pending_task(existing, task.task_id):
        return path
    new_content = _append_task_to_markdown(existing, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")
    append_memory("self-improve", f"[aria-worker] {task.task_id} local md → {path}")
    return path


def _task_from_record(record: dict[str, Any]) -> WorkerTask:
    return WorkerTask(
        task_id=str(record.get("task_id") or "task"),
        title=str(record.get("title") or ""),
        source=str(record.get("source") or ""),
        problem=str(record.get("problem") or ""),
        action=str(record.get("action") or ""),
        priority=str(record.get("priority") or "normal"),
        repos=tuple(record.get("repos") or ()),
        files=tuple(record.get("files") or ()),
        acceptance=tuple(record.get("acceptance") or ()),
        issue_url=str(record.get("issue_url") or ""),
        pr_url=str(record.get("pr_url") or ""),
        context=str(record.get("context") or ""),
        created_at=str(record.get("created_at") or ""),
    )


def _backfill_task_from_feedback_jsonl(task_id: str) -> WorkerTask | None:
    """Reconstruit une tâche community_feedback si le jsonl worker est incomplet."""
    from aria_core.paths import data_dir

    fb_path = data_dir() / "community-feedback.jsonl"
    if not fb_path.is_file():
        return None
    for line in fb_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("worker_task_id") or "") != task_id:
            continue
        text = str(rec.get("text") or "").strip()
        if not text:
            return None
        handle = str(rec.get("handle") or "").strip().lstrip("@")
        score = int(rec.get("score") or 0)
        handle_bit = f" (@{handle})" if handle else ""
        return WorkerTask(
            task_id=task_id,
            title=text[:120],
            source="community_feedback",
            problem=f"Feedback communauté site{handle_bit} — score {score}/100\n\n{text}",
            action=(
                "Évaluer l'idée communauté ; si alignée vision ZHC/Vanguard, préparer workflow "
                "ouvrier (spec courte, fichiers cibles, critères d'acceptation) puis implémenter."
            ),
            priority="normal",
            repos=("aria-vanguard", "aria-sandbox"),
            acceptance=(
                "Décision documentée (ship / defer / decline)",
                "Si ship : PR ou commit + JOURNAL.md",
            ),
            context=f"visitor={rec.get('visitor_id') or 'anon'} source={rec.get('source') or 'vanguard_site'}",
            created_at=str(rec.get("at") or "")[:19].replace("T", " ") + "Z" if rec.get("at") else "",
        )
    return None


def _cap_gap_runtime_resolved_sync(task_id: str) -> bool:
    """Skip re-sync worker si capability_gap déjà OK (anti-boucle)."""
    tid = (task_id or "").strip()
    if not tid.startswith("cap-gap-"):
        return False
    cap = tid[len("cap-gap-") :]
    try:
        import asyncio

        from aria_core.capability_gap import gap_runtime_resolved

        return asyncio.run(gap_runtime_resolved(cap))
    except Exception:
        return False


def sync_pending_local_tasks_to_md() -> list[str]:
    """Rejoue les tâches pending du jsonl local vers ARIA-WORKER.md (session ouvrier)."""
    jsonl = _local_jsonl()
    if not jsonl.is_file():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = str(rec.get("task_id") or "")
        if tid:
            latest[tid] = rec

    synced: list[str] = []
    path = resolve_local_worker_md()
    existing = path.read_text(encoding="utf-8") if path and path.is_file() else ""
    for tid, rec in latest.items():
        if str(rec.get("status") or "") != "pending":
            continue
        if _cap_gap_runtime_resolved_sync(tid):
            _append_local_record(
                _task_from_record(rec),
                status="skipped_resolved",
                extra={"reason": "runtime_ok"},
            )
            continue
        if path and _has_pending_task(existing, tid):
            continue
        if not rec.get("problem") and not rec.get("action"):
            backfill = _backfill_task_from_feedback_jsonl(tid)
            if not backfill:
                continue
            task = backfill
        else:
            task = _task_from_record(rec)
        written = _write_task_to_local_md(task)
        if written:
            synced.append(tid)
            if path:
                existing = written.read_text(encoding="utf-8")
    return synced


def _append_local_record(task: WorkerTask, *, status: str, extra: dict[str, Any] | None = None) -> None:
    record = {
        "task_id": task.task_id,
        "title": task.title,
        "source": task.source,
        "status": status,
        "created_at": task.created_at,
        "problem": task.problem,
        "action": task.action,
        "priority": task.priority,
        "repos": list(task.repos),
        "files": list(task.files),
        "acceptance": list(task.acceptance),
        "issue_url": task.issue_url,
        "pr_url": task.pr_url,
        "context": task.context,
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
    if _cap_gap_runtime_resolved_sync(clean_id):
        return {
            "task_id": clean_id,
            "title": title.strip()[:200],
            "status": "skipped_resolved",
            "reason": "runtime_ok",
        }
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
    local_md = _write_task_to_local_md(task)
    if local_md:
        result["local_worker_md"] = str(local_md)

    if not github_configured():
        append_memory("self-improve", f"[aria-worker] {task.task_id} local only (no GITHUB_TOKEN)")
        result["status"] = "local_md" if local_md else "local_only"
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
    elif local_md:
        result["status"] = "local_md"
        result["errors"] = errors
        append_memory(
            "self-improve",
            f"[aria-worker] {task.task_id} push failed — fallback local md",
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
    from aria_core.capability_gap import CAPABILITY_SPECS, gap_runtime_resolved

    if await gap_runtime_resolved(capability_id):
        return {
            "status": "skipped_resolved",
            "task_id": f"cap-gap-{capability_id}",
            "capability_id": capability_id,
            "reason": "runtime_ok",
        }

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
    elif result.get("status") in ("local_only", "local_md"):
        if result.get("local_worker_md"):
            msg += f"Local MD : {result.get('local_worker_md')}\n"
        msg += f"Local jsonl : {result.get('local_jsonl')}\n"
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
    status = result.get("status")
    if status == "pushed":
        pushed = (result.get("pushed") or [{}])[0]
        lines.append(f"File : {pushed.get('repo')}/{pushed.get('path')}")
    elif status == "local_md" and result.get("local_worker_md"):
        lines.append(f"File locale : {result.get('local_worker_md')}")
    if lang == "fr":
        lines.append("L'ouvrier traitera `sessions/ARIA-WORKER.md` à la prochaine session.")
    else:
        lines.append("Worker will process sessions/ARIA-WORKER.md next session.")
    return "\n".join(lines)