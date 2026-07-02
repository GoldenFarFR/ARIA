"""Ingest repo local → cognitive + vector + rapport vérifiable (preuve anti-hallucination)."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from aria_core.memory import append_memory
from aria_core.memory.vector.chroma_store import vector_store_status
from aria_core.memory.vector.ingest import ingest_approved_item
from aria_core.paths import memory_dir

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "venv", "__pycache__", "chroma", ".aria-test-data",
    "dist", "build", ".letta", ".pytest_cache", "site-packages",
})
_TEXT_SUFFIXES = frozenset({
    ".md", ".yaml", ".yml", ".txt", ".json",
})
_CODE_SUFFIXES = frozenset({".py", ".ps1"})
_MAX_FILE_BYTES = 120_000
_MAX_ITEMS = 48
_MAX_CONTENT_CHARS = 700
_PRIORITY_REL = (
    "collegue-memoire/COLLEGUE.md",
    "collegue-memoire/sessions/HANDOFF.md",
    "VISION.md",
    "vanguard/VISION.md",
    "packages/aria-core/src/aria_core/knowledge/aria_values.yaml",
    "packages/aria-core/src/aria_core/knowledge/aria_goals.yaml",
    "packages/aria-core/docs/ARCHITECTURE.md",
)

_INGEST_RE = re.compile(
    r"(?:"
    r"/ingest\b|ingest[- ]?repo|ingérer.*repo|ingest.*mémoire|ingest.*memoire|"
    r"alimente.*mémoire|alimente.*memoire|alimenter.*mémoire|"
    r"abord[eé].*données|lis(?:er)?\s+tout.*github|parcourir.*repo|"
    r"crée.*mémoire.*github|creer.*memoire.*github|"
    r"indexe.*repo|indexer.*repo"
    r")",
    re.IGNORECASE,
)
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/\n\r]+\\)*[^\\/\n\r]+")


def wants_ingest_repo(message: str) -> bool:
    return bool(_INGEST_RE.search((message or "").strip()))


def _default_repo_root() -> Path:
    for key in ("ARIA_REPO_ROOT",):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            p = Path(raw)
            if p.is_dir():
                return p.resolve()
    default = Path.home() / "GitHub-Repos" / "ARIA"
    if default.is_dir():
        return default.resolve()
    return Path.cwd().resolve()


def _resolve_repo_path(message: str) -> Path:
    for match in _WIN_PATH_RE.findall(message):
        p = Path(match)
        if p.is_dir():
            return p.resolve()
    lower = message.lower()
    if "collegue" in lower:
        root = _default_repo_root()
        cm = root / "collegue-memoire"
        if cm.is_dir():
            return cm.resolve()
    return _default_repo_root()


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _should_skip_dir(name: str) -> bool:
    return name.lower() in _SKIP_DIRS or name.startswith(".")


def _collect_candidates(root: Path) -> list[Path]:
    found: dict[str, Path] = {}

    for rel in _PRIORITY_REL:
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            found[_rel(p, root)] = p

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(_should_skip_dir(part) for part in path.parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        rel = _rel(path, root)
        suffix = path.suffix.lower()
        if suffix in _TEXT_SUFFIXES:
            found.setdefault(rel, path)
        elif suffix in _CODE_SUFFIXES and (
            "knowledge" in rel or "skills" in rel or rel.endswith("SKILL.md")
        ):
            found.setdefault(rel, path)
        if len(found) >= _MAX_ITEMS * 2:
            break

    ordered: list[Path] = []
    for rel in _PRIORITY_REL:
        if rel in found:
            ordered.append(found.pop(rel))
    rest = sorted(found.values(), key=lambda p: _rel(p, root))
    for p in rest:
        if len(ordered) >= _MAX_ITEMS:
            break
        ordered.append(p)
    return ordered


def _read_snippet(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > _MAX_CONTENT_CHARS:
        return text[:_MAX_CONTENT_CHARS] + "…"
    return text


def _report_path() -> Path:
    return memory_dir() / "ingest_repo_reports.jsonl"


async def execute_ingest_repo(message: str, lang: str = "fr") -> tuple[str, dict]:
    from aria_core.knowledge.cognitive import add_knowledge

    t0 = time.monotonic()
    root = _resolve_repo_path(message)
    if not root.is_dir():
        err = f"Repo introuvable : {root}"
        return err, {"ok": False, "error": "repo_not_found", "root": str(root)}

    before = vector_store_status()
    vector_before = int(before.get("collection_count") or 0)
    files = _collect_candidates(root)
    if not files:
        return f"Aucun fichier texte indexable sous {root}", {"ok": False, "root": str(root)}

    stored_ids: list[str] = []
    vector_doc_ids: list[str] = []
    files_read: list[str] = []

    for path in files:
        rel = _rel(path, root)
        snippet = _read_snippet(path)
        if not snippet:
            continue
        files_read.append(rel)
        content = f"[ingest-repo] {rel}\n{snippet}"
        item = await add_knowledge(
            source="operator",
            topic="repo-ingest",
            content=content,
            confidence=0.92,
            approved=True,
        )
        stored_ids.append(item.id)
        doc_id = await ingest_approved_item(item.id)
        if doc_id:
            vector_doc_ids.append(doc_id)

    after = vector_store_status()
    vector_after = int(after.get("collection_count") or 0)
    elapsed = round(time.monotonic() - t0, 2)
    report_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "id": report_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "files_read": files_read,
        "files_count": len(files_read),
        "cognitive_added": len(stored_ids),
        "cognitive_ids": stored_ids,
        "vector_docs": len(vector_doc_ids),
        "vector_before": vector_before,
        "vector_after": vector_after,
        "elapsed_seconds": elapsed,
        "report_path": str(_report_path()),
    }
    _report_path().parent.mkdir(parents=True, exist_ok=True)
    with _report_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")

    append_memory(
        "ingest_repo",
        f"Ingest {report_id}: {len(files_read)} fichiers, "
        f"{len(stored_ids)} cognitive, vector {vector_before}→{vector_after}",
    )

    if lang == "fr":
        lines = [
            "═══ INGEST-REPO — PREUVE ═══",
            f"Racine      : {root}",
            f"Fichiers    : {len(files_read)} lus",
            f"Cognitive   : {len(stored_ids)} entrées approuvées",
            f"Vector      : {vector_before} → {vector_after} (+{vector_after - vector_before})",
            f"Durée       : {elapsed}s",
            f"Rapport     : {_report_path()}",
            f"ID          : {report_id}",
            "",
            "Échantillon :",
        ]
        for rel in files_read[:8]:
            lines.append(f"  • {rel}")
        if len(files_read) > 8:
            lines.append(f"  … +{len(files_read) - 8} autres")
        lines.append("═══════════════════════════")
        text = "\n".join(lines)
    else:
        text = (
            f"INGEST-REPO proof: {len(files_read)} files, "
            f"{len(stored_ids)} cognitive, vector {vector_before}→{vector_after}, "
            f"report {_report_path()}"
        )

    return text, {"ok": True, **report}