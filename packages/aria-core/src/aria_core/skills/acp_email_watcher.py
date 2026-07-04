"""ACP email watcher — détecter jobs via aria_vanguard_zhc@agents.world (mode dégradé)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.paths import memory_dir
from aria_core.skills.acp_cli import email_inbox, email_search, is_acp_available

_WATCH_RE = re.compile(
    r"(?i)(?:"
    r"surveill(?:er|e)\s+(?:email|mail|bo[iî]te)\s+acp|"
    r"watch\s+acp\s+email|"
    r"email\s+jobs?\s+acp|"
    r"poll\s+email\s+acp"
    r")"
)

_JOB_ID_RE = re.compile(r"\b(0x[a-fA-F0-9]{16,})\b")
_OFFERING_RE = re.compile(
    r"(?i)\b(analyse_lite_x1|analyse_full_x1|veille_zhc_x1)\b"
)

_SEARCH_QUERIES: tuple[str, ...] = (
    "job funded",
    "new job",
    "awaiting provider",
    "deliverable",
    "ACP",
    "Virtuals",
)

_JOB_HINTS = (
    "job",
    "funded",
    "deliverable",
    "provider",
    "escrow",
    "marketplace",
    "acp",
    "virtuals",
    "awaiting",
)

_STATE_PATH = memory_dir() / "acp_email_watch_state.json"


def wants_acp_email_watch(message: str) -> bool:
    return bool(_WATCH_RE.search((message or "").strip()))


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"seen_ids": [], "alerts": []}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_ids": [], "alerts": []}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["seen_ids"] = list(state.get("seen_ids") or [])[-500:]
    state["alerts"] = list(state.get("alerts") or [])[-100:]
    _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _message_blob(msg: dict[str, Any]) -> str:
    parts = [
        str(msg.get("subject") or ""),
        str(msg.get("snippet") or ""),
        str(msg.get("preview") or ""),
        str(msg.get("body") or ""),
        str(msg.get("text") or ""),
        str(msg.get("from") or ""),
    ]
    return " ".join(parts)


def _message_id(msg: dict[str, Any]) -> str:
    for key in ("id", "messageId", "message_id", "threadId", "thread_id"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    subj = str(msg.get("subject") or "")[:80]
    ts = str(msg.get("createdAt") or msg.get("date") or "")
    return f"{subj}:{ts}"


def _looks_like_job_email(msg: dict[str, Any]) -> bool:
    blob = _message_blob(msg).lower()
    return any(h in blob for h in _JOB_HINTS)


def _extract_alert(msg: dict[str, Any]) -> dict[str, Any] | None:
    if not _looks_like_job_email(msg):
        return None
    blob = _message_blob(msg)
    job_ids = _JOB_ID_RE.findall(blob)
    offering_m = _OFFERING_RE.search(blob)
    return {
        "message_id": _message_id(msg),
        "subject": str(msg.get("subject") or "")[:200],
        "job_ids": job_ids[:3],
        "offering": offering_m.group(1).lower() if offering_m else "",
        "snippet": str(msg.get("snippet") or msg.get("preview") or "")[:300],
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def _unwrap_messages(data: dict | list | None) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("messages", "results", "items", "data"):
        block = data.get(key)
        if isinstance(block, list):
            return [m for m in block if isinstance(m, dict)]
    return []


def _collect_messages() -> tuple[list[dict[str, Any]], list[str]]:
    messages: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_keys: set[str] = set()

    inbox, err_inbox = email_inbox()
    if err_inbox:
        errors.append(f"inbox: {err_inbox[:120]}")
    else:
        for msg in _unwrap_messages(inbox):
            mid = _message_id(msg)
            if mid not in seen_keys:
                seen_keys.add(mid)
                messages.append(msg)

    for query in _SEARCH_QUERIES:
        data, err = email_search(query)
        if err:
            errors.append(f"{query}: {err[:80]}")
            continue
        for msg in _unwrap_messages(data):
            mid = _message_id(msg)
            if mid not in seen_keys:
                seen_keys.add(mid)
                messages.append(msg)
    return messages, errors


async def run_email_watch(*, notify_new: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "new_alerts": [],
        "scanned": 0,
        "errors": [],
    }
    if not is_acp_available():
        result["errors"].append("acp-cli indisponible")
        return result

    state = _load_state()
    seen: set[str] = set(state.get("seen_ids") or [])
    messages, errors = _collect_messages()
    result["scanned"] = len(messages)
    result["errors"] = errors[:6]

    new_alerts: list[dict[str, Any]] = []
    for msg in messages:
        mid = _message_id(msg)
        alert = _extract_alert(msg)
        if not alert:
            continue
        if mid in seen:
            continue
        seen.add(mid)
        new_alerts.append(alert)

    if new_alerts:
        state["alerts"] = (state.get("alerts") or []) + new_alerts
        if notify_new:
            for alert in new_alerts:
                append_memory(
                    "acp_email",
                    f"[job_alert] ids={alert.get('job_ids')} subj={alert.get('subject', '')[:80]}",
                )

    state["seen_ids"] = list(seen)
    state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    result["ok"] = True
    result["new_alerts"] = new_alerts
    return result


async def execute_acp_email_watch(message: str, lang: str = "fr") -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    scan = await run_email_watch()
    alerts = scan.get("new_alerts") or []

    if lang_key == "fr":
        lines = [
            "═══ ACP EMAIL WATCH ═══",
            "",
            f"Messages scannés : {scan.get('scanned', 0)}",
            f"Nouvelles alertes job : {len(alerts)}",
        ]
        if scan.get("errors"):
            lines.append("Erreurs (extrait) :")
            for e in scan["errors"][:3]:
                lines.append(f"  - {e}")
        if alerts:
            lines.append("")
            for alert in alerts[:5]:
                jids = ", ".join(alert.get("job_ids") or []) or "(id à copier depuis Hermès)"
                lines.append(f"• {alert.get('subject', '?')[:100]}")
                lines.append(f"  Job(s) : {jids}")
                if alert.get("offering"):
                    lines.append(f"  Offre : {alert['offering']}")
                lines.append(
                    f"  → préparer job acp {jids.split(',')[0] if jids != '(id à copier depuis Hermès)' else '<job_id>'} "
                    f"offre {alert.get('offering') or 'analyse_lite_x1'}"
                )
        else:
            lines.append("")
            lines.append(
                "Aucun nouveau mail job. Vérifie Hermès si un job est en attente, "
                "puis : « préparer job acp 0x… offre … contract 0x… »"
            )
        lines.append("")
        lines.append("Heartbeat : acp_email_watch (10 min) · mode dégradé Virtuals Privy 500")
        return "\n".join(lines), {"acp": "email_watch", "scan": scan}

    lines = [
        "ACP EMAIL WATCH",
        f"Scanned: {scan.get('scanned', 0)} · new alerts: {len(alerts)}",
    ]
    return "\n".join(lines), {"acp": "email_watch", "scan": scan}