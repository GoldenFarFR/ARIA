"""Sync aria-core → Letta archival (journal, reflections, pitfalls). Sprint 2."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aria_config import ARIA_REPO_ROOT
from letta_api import insert_archival_memory, is_letta_available
from ouvrier_memory import bootstrap_aria_core_runtime

_STATE_NAME = "sync-letta-state.json"
_MAX_PASSAGES = 24
_MAX_LINE = 900


def _state_path() -> Path:
    bootstrap_aria_core_runtime()
    from aria_core.paths import memory_dir

    return memory_dir() / _STATE_NAME


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {"synced_hashes": [], "last_sync_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"synced_hashes": [], "last_sync_at": None}
        hashes = data.get("synced_hashes") or []
        if not isinstance(hashes, list):
            hashes = []
        return {"synced_hashes": [str(h) for h in hashes][-500:], "last_sync_at": data.get("last_sync_at")}
    except Exception:
        return {"synced_hashes": [], "last_sync_at": None}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _journal_passages() -> list[tuple[str, str]]:
    path = ARIA_REPO_ROOT / "collegue-memoire" / "JOURNAL.md"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[tuple[str, str]] = []
    for line in lines[-12:]:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("|") or text.startswith("---"):
            continue
        out.append(("journal", f"[aria-core/journal] {text[:_MAX_LINE]}"))
    return out


def _reflection_passages() -> list[tuple[str, str]]:
    bootstrap_aria_core_runtime()
    from aria_core.memory.reflection import read_explicit_reflections

    rows = read_explicit_reflections(limit=8)
    out: list[tuple[str, str]] = []
    for row in rows:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        ctx = str(row.get("context") or "session")
        outcome = str(row.get("outcome") or "note")
        out.append(
            (
                "reflection",
                f"[aria-core/reflection] {ctx}/{outcome} — {content[:_MAX_LINE]}",
            )
        )
    return out


def _pitfall_passages() -> list[tuple[str, str]]:
    path = (
        ARIA_REPO_ROOT
        / "packages"
        / "aria-core"
        / "src"
        / "aria_core"
        / "knowledge"
        / "operator_pitfalls.yaml"
    )
    if not path.is_file():
        return []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    raw = doc.get("pitfalls") or []
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    for item in raw[-6:]:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "?")
        lesson = " ".join(str(item.get("lesson") or "").split())
        never = " ".join(str(item.get("never") or "").split())
        if not lesson:
            continue
        text = f"[aria-core/pitfall] {pid} — {lesson[:500]}"
        if never:
            text += f" | Jamais : {never[:280]}"
        out.append(("pitfall", text[:_MAX_LINE]))
    return out


def collect_passages() -> list[tuple[str, str]]:
    seen: set[str] = set()
    merged: list[tuple[str, str]] = []
    for source, text in (
        _reflection_passages()
        + _journal_passages()
        + _pitfall_passages()
    ):
        key = _hash_text(text)
        if key in seen:
            continue
        seen.add(key)
        merged.append((source, text))
    return merged[:_MAX_PASSAGES]


def _resolve_agent_ids() -> list[str]:
    ids: list[str] = []
    ouvrier_cfg = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
    if ouvrier_cfg.is_file():
        try:
            data = json.loads(ouvrier_cfg.read_text(encoding="utf-8"))
            aid = str(data.get("agent_id") or "").strip()
            if aid:
                ids.append(aid)
        except Exception:
            pass
    sync_all = __import__("os").environ.get("ARIA_LETTA_SYNC_AGENTS", "").strip().lower() in (
        "all",
        "1",
        "true",
        "yes",
    )
    agents_cfg = ARIA_REPO_ROOT / "letta-orchestrator" / "agents_config.json"
    if sync_all and agents_cfg.is_file():
        try:
            data = json.loads(agents_cfg.read_text(encoding="utf-8"))
            for key in ("scout", "grok", "core"):
                aid = str(data.get(key) or "").strip()
                if aid and aid not in ids:
                    ids.append(aid)
        except Exception:
            pass
    return ids


def run_sync(*, dry_run: bool = False) -> dict:
    if not is_letta_available():
        return {"ok": False, "reason": "letta_down", "inserted": 0, "skipped": 0}

    agent_ids = _resolve_agent_ids()
    if not agent_ids:
        return {"ok": False, "reason": "no_agent_id", "inserted": 0, "skipped": 0}

    state = _load_state()
    known = set(state.get("synced_hashes") or [])
    passages = collect_passages()
    inserted = 0
    skipped = 0
    errors: list[str] = []

    for _source, text in passages:
        digest = _hash_text(text)
        if digest in known:
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            known.add(digest)
            continue
        ok_any = False
        for agent_id in agent_ids:
            try:
                insert_archival_memory(agent_id, text)
                ok_any = True
            except Exception as exc:
                errors.append(f"{agent_id[:12]}: {exc}")
        if ok_any:
            inserted += 1
            known.add(digest)
        else:
            skipped += 1

    state["synced_hashes"] = list(known)[-500:]
    state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    if not dry_run and (inserted or skipped):
        _save_state(state)

    return {
        "ok": inserted > 0 or skipped > 0,
        "reason": "synced" if inserted else ("noop" if skipped else "empty"),
        "inserted": inserted,
        "skipped": skipped,
        "agents": len(agent_ids),
        "errors": errors[:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync aria-core → Letta archival")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_sync(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        if report.get("ok"):
            print(
                f"sync-letta OK — inserted={report['inserted']} "
                f"skipped={report['skipped']} agents={report['agents']}"
            )
        else:
            print(f"sync-letta skip — {report.get('reason')}")
        for err in report.get("errors") or []:
            print(f"  erreur: {err}", file=sys.stderr)
    return 0 if report.get("ok") or report.get("reason") in ("noop", "letta_down") else 1


if __name__ == "__main__":
    raise SystemExit(main())