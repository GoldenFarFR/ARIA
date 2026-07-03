"""Letta-2 — critique session aria-core → pending-lessons.md (Sprint 3)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_config import ARIA_REPO_ROOT, bridge_api_keys
from letta_api import insert_archival_memory, is_letta_available, send_message
from ouvrier_memory import bootstrap_aria_core_runtime
from ouvrier_runner import _chat, _resolve_groq

PERSONA_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "letta2_persona.md"
CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "letta2_config.json"
PENDING_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "pending-lessons.md"
_STATE_NAME = "sync-letta2-critique-state.json"
_MIN_CONTEXT_CHARS = 120


def _state_path() -> Path:
    bootstrap_aria_core_runtime()
    from aria_core.paths import memory_dir

    return memory_dir() / _STATE_NAME


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_critique_agent_id() -> str | None:
    if not CONFIG_PATH.is_file():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        aid = str(data.get("agent_id") or "").strip()
        return aid or None
    except Exception:
        return None


def _journal_block() -> str:
    path = ARIA_REPO_ROOT / "collegue-memoire" / "JOURNAL.md"
    if not path.is_file():
        return ""
    lines = [
        ln.strip()
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        if ln.strip() and not ln.startswith("#") and not ln.startswith("|")
    ]
    if not lines:
        return ""
    return "## Journal (extrait)\n" + "\n".join(f"- {ln}" for ln in lines)


def _reflections_block() -> str:
    bootstrap_aria_core_runtime()
    from aria_core.memory.reflection import read_explicit_reflections

    rows = read_explicit_reflections(limit=12)
    if not rows:
        return ""
    lines = ["## Reflections aria-core"]
    for row in rows:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        ctx = row.get("context") or "?"
        outcome = row.get("outcome") or "?"
        at = row.get("at") or ""
        lines.append(f"- [{at}] {ctx}/{outcome} — {content[:400]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _pitfalls_block() -> str:
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
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    ids = re.findall(r"^\s+-\s+id:\s*(\S+)", text, re.MULTILINE)[-4:]
    if not ids:
        return ""
    return "## Pitfalls SSOT (ids récents)\n" + ", ".join(ids)


def _kart_block() -> str:
    path = ARIA_REPO_ROOT / "memory" / "kart-session.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    user = str(data.get("last_user") or "").strip()
    reply = str(data.get("last_reply") or "").strip()
    if not user:
        return ""
    return (
        "## Dernier tour KART\n"
        f"- Sylvain : {user[:300]}\n"
        f"- Réponse : {reply[:400]}"
    )


def build_critique_context() -> str:
    parts = [_journal_block(), _reflections_block(), _pitfalls_block(), _kart_block()]
    body = "\n\n".join(p for p in parts if p.strip())
    return body.strip()


def _context_hash(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:20]


def _groq_critique(context: str) -> str:
    bridge_api_keys()
    groq = _resolve_groq()
    if not groq:
        return ""
    persona = PERSONA_PATH.read_text(encoding="utf-8") if PERSONA_PATH.is_file() else ""
    _, url, api_key, model = groq
    user = (
        "Analyse ce contexte session ARIA et produis 1 à 3 leçons (format persona).\n\n"
        f"{context[:12000]}"
    )
    try:
        data = _chat(
            url,
            api_key,
            model,
            [
                {"role": "system", "content": persona},
                {"role": "user", "content": user},
            ],
            tools=False,
        )
    except RuntimeError:
        return ""
    choice = data.get("choices", [{}])[0].get("message", {})
    return (choice.get("content") or "").strip()


def _letta_critique(context: str) -> str:
    agent_id = _load_critique_agent_id()
    if not agent_id or not is_letta_available():
        return ""
    prompt = (
        "Critique session — propose leçons format persona (1-3 max).\n\n"
        f"{context[:10000]}"
    )
    try:
        return (send_message(agent_id, prompt) or "").strip()
    except Exception:
        return ""


def _pick_critique_text(context: str) -> tuple[str, str]:
    """Retourne (texte, moteur)."""
    text = _groq_critique(context)
    if text and "AUCUNE_LECON" not in text.upper():
        return text, "groq"
    letta_text = _letta_critique(context)
    if letta_text and "AUCUNE_LECON" not in letta_text.upper():
        return letta_text, "letta"
    if text:
        return text, "groq"
    return letta_text, "letta" if letta_text else "none"


def _append_pending_lessons(body: str, *, engine: str) -> Path:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"\n\n---\n\n## Critique {stamp} (moteur: {engine})\n\n"
    if not PENDING_PATH.is_file():
        intro = (
            "# Leçons en attente — validation Sylvain\n\n"
            "> Généré par ARIA-Critique (Letta-2). Valider puis ship vers core "
            "(reflection, pitfall, COLLEGUE).\n"
        )
        PENDING_PATH.write_text(intro + header + body.strip() + "\n", encoding="utf-8")
    else:
        with PENDING_PATH.open("a", encoding="utf-8") as f:
            f.write(header + body.strip() + "\n")
    return PENDING_PATH


def _sync_critique_to_archival(text: str) -> None:
    agent_id = _load_critique_agent_id()
    if not agent_id or not is_letta_available():
        return
    summary = text[:2000]
    try:
        insert_archival_memory(agent_id, f"[letta2/critique] {summary}")
    except Exception:
        pass


def run_critique(*, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    context = build_critique_context()
    if len(context) < _MIN_CONTEXT_CHARS:
        return {"ok": False, "reason": "context_thin", "chars": len(context)}

    digest = _context_hash(context)
    state = _load_state()
    if not force and state.get("last_context_hash") == digest:
        return {"ok": True, "reason": "unchanged", "chars": len(context)}

    if dry_run:
        return {"ok": True, "reason": "dry_run", "chars": len(context), "hash": digest}

    body, engine = _pick_critique_text(context)
    if not body:
        return {
            "ok": False,
            "reason": "groq_quota_or_empty",
            "chars": len(context),
            "hint": "Réessaie plus tard ou lance avec Letta seul (agent ARIA-Critique).",
        }
    if "AUCUNE_LECON" in body.upper():
        state["last_context_hash"] = digest
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        state["last_result"] = "aucune_lecon"
        _save_state(state)
        return {"ok": True, "reason": "aucune_lecon", "engine": engine}

    path = _append_pending_lessons(body, engine=engine)
    _sync_critique_to_archival(body)
    state["last_context_hash"] = digest
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_result"] = "lessons"
    state["pending_path"] = str(path)
    _save_state(state)

    lesson_count = len(re.findall(r"(?m)^###\s+Leçon", body))
    return {
        "ok": True,
        "reason": "lessons",
        "engine": engine,
        "lessons": max(lesson_count, 1),
        "path": str(path),
        "chars": len(context),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ARIA-Critique Letta-2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore context hash dedup")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_critique(dry_run=args.dry_run, force=args.force)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        reason = report.get("reason")
        if reason == "lessons":
            print(
                f"letta2 OK — {report.get('lessons')} leçon(s) via {report.get('engine')} "
                f"→ {report.get('path')}"
            )
        elif reason == "aucune_lecon":
            print("letta2 — aucune leçon nouvelle (contexte analysé)")
        elif reason == "unchanged":
            print("letta2 — contexte inchangé, skip")
        else:
            print(f"letta2 skip — {reason}")
    soft_skip = {"unchanged", "aucune_lecon", "groq_quota_or_empty", "context_thin"}
    return 0 if report.get("ok") or report.get("reason") in soft_skip else 1


if __name__ == "__main__":
    raise SystemExit(main())