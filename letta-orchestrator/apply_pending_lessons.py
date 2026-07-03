"""Sprint 4 — applique les leçons validées de pending-lessons.md vers aria-core."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_config import ARIA_REPO_ROOT
from ouvrier_memory import bootstrap_aria_core_runtime

PENDING_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "pending-lessons.md"
COLLEGUE_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "COLLEGUE.md"
JOURNAL_SCRIPT = (
    ARIA_REPO_ROOT / "skills" / ".grok" / "skills" / "journal-de-bord" / "scripts" / "append.ps1"
)

_FIELD_RE = re.compile(r"(?im)^-\s+\*\*(.+?)\*\*\s*:\s*(.+)$")
_LESSON_SPLIT_RE = re.compile(r"(?m)^###\s+Leçon\b")


def _slug_id(title: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "lesson").lower()).strip("-")
    return (raw[:48] or "lesson") + f"-{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def _parse_status(block: str) -> str:
    for key, val in _parse_fields(block).items():
        if key.lower() == "statut":
            return val.strip().lower()
    return "pending"


def _parse_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(block):
        fields[m.group(1).strip()] = m.group(2).strip()
    return fields


def parse_lessons(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    parts = _LESSON_SPLIT_RE.split(text)
    lessons: list[dict[str, Any]] = []
    for part in parts[1:]:
        block = part.strip()
        if not block:
            continue
        title_line = block.splitlines()[0].strip()
        title = re.sub(r"^[-—:\s]+", "", title_line).strip() or "sans titre"
        fields = _parse_fields(block)
        ship = fields.get("Ship core", fields.get("ship core", "reflection")).lower()
        lessons.append(
            {
                "title": title,
                "fields": fields,
                "ship": ship,
                "status": _parse_status(block),
                "block": f"### Leçon {block}",
            }
        )
    return lessons


def _load_pending() -> str:
    if not PENDING_PATH.is_file():
        return ""
    return PENDING_PATH.read_text(encoding="utf-8", errors="replace")


def _save_pending(text: str) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(text, encoding="utf-8")


def _set_lesson_status(content: str, index: int, status: str) -> str:
    """index 1-based."""
    lessons = parse_lessons(content)
    if index < 1 or index > len(lessons):
        return content
    target = lessons[index - 1]
    block = target["block"]
    fields = target["fields"]
    if re.search(r"(?im)^-\s+\*\*Statut\*\*\s*:", block):
        new_block = re.sub(
            r"(?im)^-\s+\*\*Statut\*\*\s*:\s*.+$",
            f"- **Statut** : {status}",
            block,
            count=1,
        )
    else:
        new_block = block.rstrip() + f"\n- **Statut** : {status}\n"
    return content.replace(block, new_block, 1)


def list_lessons() -> list[dict[str, Any]]:
    return parse_lessons(_load_pending())


def approve_lesson(index: int) -> bool:
    content = _load_pending()
    if not content:
        return False
    updated = _set_lesson_status(content, index, "approved")
    if updated == content:
        return False
    _save_pending(updated)
    return True


def _apply_reflection(lesson: dict[str, Any]) -> str:
    bootstrap_aria_core_runtime()
    from aria_core.memory.reflection import append_reflection

    fields = lesson["fields"]
    parts = [
        lesson["title"],
        fields.get("Constat", ""),
        fields.get("Tu as fait X", fields.get("Tu as fait", "")),
        fields.get("Mieux", fields.get("Mieux : Y", "")),
    ]
    body = " — ".join(p.strip() for p in parts if p.strip())[:580]
    append_reflection(body, context="letta2-apply", outcome="lesson")
    return "reflection"


def _apply_pitfall(lesson: dict[str, Any]) -> str:
    from aria_core.knowledge.operator_runbook import append_pitfall_if_new

    fields = lesson["fields"]
    pid = _slug_id(lesson["title"])
    entry = {
        "id": pid,
        "severity": "medium",
        "lesson": fields.get("Constat", lesson["title"])[:500],
        "fix": fields.get("Mieux", fields.get("Mieux : Y", ""))[:500],
        "verify": "check-aria-status.ps1 ou preuve CLI",
        "never": fields.get("Tu as fait X", fields.get("Tu as fait", ""))[:300],
    }
    if not entry["fix"]:
        entry["fix"] = lesson["title"]
    added = append_pitfall_if_new(entry)
    return "pitfall" if added else "pitfall_skip_dup"


def _apply_collegue(lesson: dict[str, Any]) -> str:
    if not COLLEGUE_PATH.is_file():
        return "collegue_missing"
    fields = lesson["fields"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mieux = fields.get("Mieux", fields.get("Mieux : Y", lesson["title"]))[:180]
    row = f"| {today} | **{lesson['title']}** — {mieux} |"
    text = COLLEGUE_PATH.read_text(encoding="utf-8")
    marker = "## Journal"
    pos = text.find(marker)
    if pos < 0:
        text = text.rstrip() + f"\n\n## Journal\n\n| Date | Décision |\n|------|----------|\n{row}\n"
    else:
        sub = text[pos:]
        sep = re.search(r"\n\|[-:]+\|[-:]+\|", sub)
        if sep:
            insert_at = pos + sep.end()
            text = text[:insert_at] + "\n" + row + text[insert_at:]
        else:
            text = text.rstrip() + f"\n{row}\n"
    COLLEGUE_PATH.write_text(text, encoding="utf-8")
    return "collegue"


def _apply_skill_route(lesson: dict[str, Any]) -> str:
    from aria_core.aria_worker_queue import WorkerTask, _write_task_to_local_md

    fields = lesson["fields"]
    task_id = f"lesson-{_slug_id(lesson['title'])}"
    task = WorkerTask(
        task_id=task_id,
        title=lesson["title"][:200],
        source="letta2_lesson",
        problem=fields.get("Constat", lesson["title"]),
        action=fields.get("Mieux", "Implémenter route déterministe / skill."),
        priority="normal",
        repos=("ARIA",),
        acceptance=("Route ou skill shipé", "Tests + JOURNAL.md"),
        context=lesson["block"][:1500],
    )
    path = _write_task_to_local_md(task)
    return "skill_route" if path else "skill_route_failed"


def apply_lesson(lesson: dict[str, Any]) -> tuple[str, str]:
    ship = (lesson.get("ship") or "reflection").lower()
    if ship == "defer":
        return "defer", "skipped"
    if ship == "pitfall":
        return _apply_pitfall(lesson), ship
    if ship == "collegue":
        return _apply_collegue(lesson), ship
    if ship == "skill_route":
        return _apply_skill_route(lesson), ship
    return _apply_reflection(lesson), "reflection"


def _append_journal(message: str) -> None:
    if not JOURNAL_SCRIPT.is_file():
        return
    import subprocess

    subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(JOURNAL_SCRIPT), "-Message", message],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def run_apply(*, indices: list[int] | None = None, approved_only: bool = True) -> dict[str, Any]:
    content = _load_pending()
    lessons = parse_lessons(content)
    if not lessons:
        return {"ok": False, "reason": "no_lessons", "applied": 0}

    targets: list[tuple[int, dict[str, Any]]] = []
    for i, lesson in enumerate(lessons, start=1):
        if indices and i not in indices:
            continue
        status = lesson.get("status", "pending")
        if indices or not approved_only:
            if status in ("done", "defer"):
                continue
        elif status != "approved":
            continue
        targets.append((i, lesson))

    if not targets:
        return {"ok": True, "reason": "nothing_to_apply", "applied": 0}

    applied: list[dict[str, Any]] = []
    updated = content
    for index, lesson in targets:
        target, ship = apply_lesson(lesson)
        updated = _set_lesson_status(updated, index, "done")
        applied.append({"index": index, "title": lesson["title"], "ship": ship, "target": target})
        _append_journal(f"apply leçon {lesson['title'][:60]} → {target}")

    _save_pending(updated)

    try:
        from sync_core_to_letta import run_sync

        run_sync()
    except Exception:
        pass

    return {"ok": True, "reason": "applied", "applied": len(applied), "items": applied}


def main() -> int:
    parser = argparse.ArgumentParser(description="Applique pending-lessons.md → aria-core")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--approve", type=int, metavar="N", help="Marquer leçon N approved")
    parser.add_argument("--apply", type=int, metavar="N", help="Appliquer leçon N (auto si pending)")
    parser.add_argument("--apply-approved", action="store_true")
    parser.add_argument("--apply-all-pending", action="store_true", help="Toutes sauf done/defer")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.list:
        rows = list_lessons()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for i, row in enumerate(rows, start=1):
                print(f"{i}. [{row.get('status')}] {row.get('title')} → ship={row.get('ship')}")
        return 0

    if args.approve:
        ok = approve_lesson(args.approve)
        print(f"approved #{args.approve}" if ok else f"échec approve #{args.approve}")
        return 0 if ok else 1

    if args.apply:
        content = _load_pending()
        lessons = parse_lessons(content)
        if args.apply < 1 or args.apply > len(lessons):
            print(f"index invalide (1-{len(lessons)})")
            return 1
        status = lessons[args.apply - 1].get("status", "pending")
        if status not in ("approved", "done"):
            updated = _set_lesson_status(content, args.apply, "approved")
            _save_pending(updated)
        report = run_apply(indices=[args.apply], approved_only=False)
    elif args.apply_all_pending:
        pending_idx = [
            i
            for i, les in enumerate(parse_lessons(_load_pending()), start=1)
            if les.get("status") not in ("done", "defer")
        ]
        for idx in pending_idx:
            approve_lesson(idx)
        report = run_apply(indices=pending_idx, approved_only=False)
    elif args.apply_approved:
        report = run_apply(approved_only=True)
    else:
        parser.print_help()
        return 0

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"apply: {report.get('reason')} — {report.get('applied', 0)} leçon(s)")
        for item in report.get("items") or []:
            print(f"  • #{item['index']} {item['title']} → {item['target']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())