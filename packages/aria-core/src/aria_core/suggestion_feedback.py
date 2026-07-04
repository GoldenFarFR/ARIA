"""Registre suggestions salut — SSOT JSONL + auto-rate worker [done]."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SuggestionStatus = Literal["open", "resolved", "dismissed"]

LEDGER_REL = Path("collegue-memoire") / "sessions" / "suggestion-ledger.jsonl"
_WORKER_DONE_RE = re.compile(r"^##\s+\[done\]\s+(.+?)\s*—\s*(.+)$", re.MULTILINE)
_WORKER_PENDING_RE = re.compile(r"^##\s+\[pending\]\s+(.+?)\s*—\s*(.+)$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)


def aria_ops_root() -> Path:
    return Path(
        os.environ.get("ARIA_OPS_ROOT", Path.home() / "GitHub-Repos" / "aria-ops")
    ).resolve()


def ledger_path() -> Path:
    return (aria_ops_root() / LEDGER_REL).resolve()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_fact(fact: str) -> str:
    text = re.sub(r"\s+", " ", (fact or "").strip().lower())
    text = re.sub(r"^\s*-\s*\[[xX ]\]\s*", "", text)
    return text[:220]


def suggestion_id(fact: str, kind: str) -> str:
    payload = f"{kind}:{normalize_fact(fact)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    fact: str
    kind: str
    status: SuggestionStatus
    first_shown: str
    last_shown: str
    show_count: int
    ratings: tuple[dict[str, Any], ...]
    avg_score: float | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        ratings = tuple(data.get("ratings") or [])
        avg = data.get("avg_score")
        if avg is None and ratings:
            avg = sum(int(r["score"]) for r in ratings) / len(ratings)
        return cls(
            id=str(data["id"]),
            fact=str(data.get("fact") or ""),
            kind=str(data.get("kind") or ""),
            status=data.get("status") or "open",
            first_shown=str(data.get("first_shown") or ""),
            last_shown=str(data.get("last_shown") or ""),
            show_count=int(data.get("show_count") or 0),
            ratings=ratings,
            avg_score=float(avg) if avg is not None else None,
        )


def _read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_ledger_index(*, path: Path | None = None) -> dict[str, LedgerEntry]:
    target = path or ledger_path()
    index: dict[str, LedgerEntry] = {}
    for row in _read_lines(target):
        event = row.get("event")
        if event in ("snapshot", "shown") and isinstance(row.get("entry"), dict):
            entry = LedgerEntry.from_dict(row["entry"])
            index[entry.id] = entry
            continue
        if event == "rating" and row.get("id"):
            sid = str(row["id"])
            base = index.get(sid)
            if not base:
                continue
            ratings = list(base.ratings)
            ratings.append(
                {
                    "score": int(row["score"]),
                    "resolver": row.get("resolver") or "ouvrier",
                    "note": row.get("note") or "",
                    "at": row.get("at") or _now_iso(),
                }
            )
            avg = sum(r["score"] for r in ratings) / len(ratings)
            status: SuggestionStatus = base.status
            if row.get("status"):
                status = row["status"]
            elif int(row["score"]) <= 1:
                status = "dismissed"
            elif int(row["score"]) >= 3:
                status = "resolved"
            index[sid] = LedgerEntry(
                id=base.id,
                fact=base.fact,
                kind=base.kind,
                status=status,
                first_shown=base.first_shown,
                last_shown=base.last_shown,
                show_count=base.show_count,
                ratings=tuple(ratings),
                avg_score=avg,
            )
    return index


def load_worker_done_task_ids(*, path: Path | None = None) -> set[str]:
    rated: set[str] = set()
    for row in _read_lines(path or ledger_path()):
        if row.get("event") == "worker_done" and row.get("task_id"):
            rated.add(str(row["task_id"]))
    return rated


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def compute_weight(entry: LedgerEntry | None) -> float:
    if entry is None:
        return 1.0
    if entry.status == "dismissed":
        return 0.05
    if entry.status == "resolved":
        if entry.avg_score is not None:
            if entry.avg_score >= 4:
                return 0.35
            if entry.avg_score <= 2:
                return 0.15
        return 0.25

    weight = 1.0
    if entry.avg_score is not None:
        if entry.avg_score >= 4.5:
            weight = 2.0
        elif entry.avg_score >= 4:
            weight = 1.7
        elif entry.avg_score >= 3:
            weight = 1.3
        elif entry.avg_score <= 2:
            weight = 0.35
        elif entry.avg_score <= 1.5:
            weight = 0.15

    if entry.show_count > 12 and entry.status == "open":
        weight *= 0.6
    elif entry.show_count > 8 and entry.status == "open":
        weight *= 0.8
    return weight


def thought_weight(fact: str, kind: str, *, index: dict[str, LedgerEntry] | None = None) -> float:
    idx = index if index is not None else load_ledger_index()
    return compute_weight(idx.get(suggestion_id(fact, kind)))


def _field(block: str, label: str) -> str:
    m = re.search(rf"\*\*{re.escape(label)}\s*:\*\*\s*(.+)", block)
    return m.group(1).strip() if m else ""


def _worker_block(text: str, task_id: str, *, pending: bool) -> str | None:
    tag = "pending" if pending else "done"
    parts = re.split(rf"(?=^##\s+\[{tag}\])", text, flags=re.MULTILINE | re.IGNORECASE)
    for part in parts:
        head = re.match(rf"^##\s+\[{tag}\]\s+{re.escape(task_id)}\b", part, re.IGNORECASE)
        if head:
            return part
    return None


def parse_worker_task_meta(text: str, task_id: str, *, pending: bool = True) -> dict[str, str]:
    block = _worker_block(text, task_id, pending=pending)
    if not block:
        return {"task_id": task_id}
    return {
        "task_id": task_id,
        "title": _field(block, "Titre"),
        "source": _field(block, "Source").strip("`"),
        "priority": _field(block, "Priorité") or _field(block, "Priorite") or "normal",
    }


def parse_worker_done_tasks(text: str) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    for m in _WORKER_DONE_RE.finditer(text):
        task_id = m.group(1).strip()
        meta = parse_worker_task_meta(text, task_id, pending=False)
        if meta.get("title"):
            tasks.append(meta)
        else:
            tasks.append({"task_id": task_id, "title": task_id, "source": "", "priority": "normal"})
    return tasks


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 3}


def _capability_token(task_id: str) -> str:
    tid = (task_id or "").strip()
    if tid.startswith("cap-gap-"):
        return tid[len("cap-gap-") :].replace("_", " ")
    return ""


def _match_score(task: dict[str, str], entry: LedgerEntry) -> float:
    title = task.get("title") or ""
    task_id = task.get("task_id") or ""
    fact = entry.fact
    low_title = title.lower()
    low_fact = fact.lower()
    if low_title and low_title in low_fact:
        return 1.0
    if low_title and low_fact in low_title:
        return 0.95
    cap = _capability_token(task_id)
    if cap and cap.replace(" ", "_") in low_fact.replace(" ", "_"):
        return 0.9
    if cap and cap in low_fact:
        return 0.85
    title_tokens = _tokens(title)
    fact_tokens = _tokens(fact)
    if not title_tokens or not fact_tokens:
        return 0.0
    overlap = len(title_tokens & fact_tokens) / max(len(title_tokens), 1)
    return overlap


def default_worker_done_score(*, priority: str, source: str) -> int:
    prio = (priority or "normal").lower()
    src = (source or "").lower()
    if prio in ("critical", "high"):
        return 5
    if "capability_gap" in src or src == "capability_gap":
        return 4
    if "community_feedback" in src:
        return 3
    return 4


def rate_suggestion(
    *,
    suggestion_id_arg: str | None = None,
    match: str | None = None,
    score: int,
    resolver: str = "cursor",
    note: str = "",
    status: SuggestionStatus | None = None,
    path: Path | None = None,
) -> list[str]:
    if score < 1 or score > 5:
        raise ValueError("score must be 1..5")
    target = path or ledger_path()
    index = load_ledger_index(path=target)
    now = _now_iso()
    targets: list[str] = []
    if suggestion_id_arg:
        targets = [suggestion_id_arg]
    elif match:
        needle = match.lower()
        for sid, entry in index.items():
            if needle in entry.fact.lower() and entry.status == "open":
                targets.append(sid)
    else:
        raise ValueError("provide suggestion_id_arg or match")

    if not targets:
        return []

    resolved_status: SuggestionStatus | None = status
    if resolved_status is None:
        if score <= 1:
            resolved_status = "dismissed"
        elif score >= 3:
            resolved_status = "resolved"
        else:
            resolved_status = "open"

    rated: list[str] = []
    for sid in targets:
        if sid not in index:
            continue
        append_event(
            target,
            {
                "event": "rating",
                "at": now,
                "id": sid,
                "score": score,
                "resolver": resolver,
                "note": note[:300],
                "status": resolved_status,
            },
        )
        rated.append(sid)
    return rated


def auto_rate_worker_done(
    task: dict[str, str],
    *,
    score: int | None = None,
    note: str = "",
    resolver: str = "auto-worker-done",
    path: Path | None = None,
) -> dict[str, Any]:
    """Note les suggestions ledger liées à une tâche worker [done]."""
    target = path or ledger_path()
    task_id = str(task.get("task_id") or "")
    if not task_id:
        return {"task_id": "", "rated": [], "score": 0}

    final_score = score if score is not None else default_worker_done_score(
        priority=str(task.get("priority") or "normal"),
        source=str(task.get("source") or ""),
    )
    index = load_ledger_index(path=target)
    matches: list[tuple[float, str]] = []
    for sid, entry in index.items():
        if entry.status not in ("open",):
            continue
        ms = _match_score(task, entry)
        if ms >= 0.45:
            matches.append((ms, sid))
    matches.sort(reverse=True)

    rated: list[str] = []
    note_text = note or f"worker [done] {task_id}"
    for _, sid in matches[:5]:
        rated.extend(
            rate_suggestion(
                suggestion_id_arg=sid,
                score=final_score,
                resolver=resolver,
                note=note_text[:300],
                status="resolved" if final_score >= 3 else None,
                path=target,
            )
        )

    append_event(
        target,
        {
            "event": "worker_done",
            "at": _now_iso(),
            "task_id": task_id,
            "title": (task.get("title") or "")[:200],
            "source": task.get("source") or "",
            "score": final_score,
            "rated_ids": rated,
            "note": note_text[:300],
        },
    )
    return {"task_id": task_id, "score": final_score, "rated": rated}


def sync_worker_done_ratings(
    worker_md: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Auto-note les [done] worker pas encore enregistrés dans le ledger."""
    already = load_worker_done_task_ids(path=path)
    results: list[dict[str, Any]] = []
    for task in parse_worker_done_tasks(worker_md):
        tid = task.get("task_id") or ""
        if not tid or tid in already:
            continue
        results.append(auto_rate_worker_done(task, path=path))
        already.add(tid)
    return results