"""ACP provider — drain events JSONL + fulfill jobs via acp-cli (PC local)."""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.skills.acp_cli import is_acp_available, job_history, provider_submit

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_config.yaml"
_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

_SUBMIT_HINTS = (
    "awaiting_deliverable",
    "awaiting_provider",
    "funded",
    "in_progress",
    "active",
    "ready_for_provider",
)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def default_events_file() -> str:
    raw = (os.environ.get("ARIA_ACP_EVENTS_FILE") or "").strip()
    if raw:
        return os.path.expandvars(raw)
    return str(Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "acp-events.jsonl")


def _state_path() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    if raw:
        return Path(os.path.expandvars(raw)) / "acp_provider_state.json"
    try:
        from aria_core.runtime import settings

        for attr in ("data_dir", "DATA_DIR"):
            val = getattr(settings, attr, None)
            if val:
                return Path(str(val)) / "acp_provider_state.json"
    except Exception:
        pass
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "GoldenFar" / "acp_provider_state.json"


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {"offset": 0, "seen_jobs": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"offset": 0, "seen_jobs": []}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["seen_jobs"] = list(state.get("seen_jobs") or [])[-200:]
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _extract_job_id(event: dict) -> str | None:
    for key in ("jobId", "job_id", "onChainJobId", "on_chain_job_id", "id"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("jobId", "job_id", "id"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _event_type(event: dict) -> str:
    for key in ("type", "event", "eventType", "name"):
        val = event.get(key)
        if isinstance(val, str):
            return val.lower()
    return ""


def _contract_from_job(history: dict) -> str:
    for container in (history, history.get("job") or {}, history.get("requirements") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("contractAddress", "contract_address", "ca"):
            val = container.get(key)
            if isinstance(val, str) and _ADDR_RE.match(val.strip()):
                return val.strip()
        req = container.get("requirements")
        if isinstance(req, dict):
            for key in ("contractAddress", "contract_address"):
                val = req.get(key)
                if isinstance(val, str) and _ADDR_RE.match(val.strip()):
                    return val.strip()
    return ""


def _offering_name(history: dict) -> str:
    for container in (history, history.get("job") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("offeringName", "offering_name", "offering"):
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _needs_provider_submit(history: dict) -> bool:
    status = ""
    for container in (history, history.get("job") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("status", "phase", "state"):
            val = container.get(key)
            if isinstance(val, str):
                status = val.lower()
                break
    if status and any(h in status for h in _SUBMIT_HINTS):
        return True
    messages = history.get("messages") or history.get("events") or []
    if isinstance(messages, list):
        for msg in reversed(messages[-5:]):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or msg.get("from") or "").lower()
            text = str(msg.get("content") or msg.get("text") or msg.get("message") or "").lower()
            if "provider" in role and "submit" in text:
                return True
            if "deliverable" in text and "await" in text:
                return True
    return bool(status)


def _heuristic_audit(contract: str, *, full: bool) -> dict[str, Any]:
    ca = (contract or "").strip()
    if not _ADDR_RE.match(ca):
        verdict = "CAUTION"
        alerts = "Adresse contrat absente ou invalide — audit limité."
        score = "35"
    elif ca.lower().endswith("0000000000000000000000000000000000000000"):
        verdict = "DANGER"
        alerts = "Adresse nulle — risque élevé."
        score = "5"
    else:
        verdict = "CAUTION"
        alerts = (
            "Scan heuristique ARIA (pas d'audit on-chain complet) : "
            "vérifier liquidité, ownership renounced, honeypot, volume réel."
        )
        score = "55"

    if full:
        report = (
            f"Audit FULL-X1 (heuristique ARIA) — CA {ca or 'N/A'}\n"
            f"Verdict : {verdict}. Score sécurité : {score}/100.\n"
            f"{alerts}\n"
            "Recommandation : confirmer via explorers (Basescan), DexScreener, "
            "et ne pas allouer de capital sans due diligence humaine."
        )
        return {
            "verdict": verdict.replace("DANGER", "AVOID").replace("CAUTION", "SPECULATIVE"),
            "securityScore": score,
            "auditReport": report,
        }
    return {
        "liteVerdict": verdict,
        "riskAlerts": alerts,
    }


def _build_deliverable(history: dict) -> dict[str, Any] | None:
    offering = _offering_name(history).lower()
    contract = _contract_from_job(history)
    full = "full" in offering or "analyse_full" in offering
    lite = "lite" in offering or "analyse_lite" in offering or not full
    if full:
        return _heuristic_audit(contract, full=True)
    if lite:
        return _heuristic_audit(contract, full=False)
    return _heuristic_audit(contract, full=False)


async def _process_job(job_id: str, *, chain_id: str) -> str | None:
    history, err = job_history(job_id, chain_id=chain_id)
    if err or not history:
        logger.warning("ACP job %s history: %s", job_id, err)
        return None
    if not _needs_provider_submit(history):
        return None
    deliverable = _build_deliverable(history)
    if not deliverable:
        return None
    ok, msg = provider_submit(job_id, deliverable, chain_id=chain_id)
    if ok:
        return f"submit:{job_id}"
    logger.warning("ACP submit %s failed: %s", job_id, msg)
    return None


def drain_events_file(path: str) -> tuple[list[dict], int]:
    """Lit les nouvelles lignes JSONL depuis le dernier offset."""
    file_path = Path(path)
    if not file_path.is_file():
        return [], 0
    state = _load_state()
    offset = int(state.get("offset") or 0)
    size = file_path.stat().st_size
    if size < offset:
        offset = 0
    events: list[dict] = []
    with file_path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        new_offset = fh.tell()
    state["offset"] = new_offset
    _save_state(state)
    return events, new_offset


async def run_provider_cycle(events_file: str | None = None) -> dict[str, Any]:
    """Drain fichier events + tentative fulfill pour chaque job nouveau/actionnable."""
    result: dict[str, Any] = {
        "ok": True,
        "processed": 0,
        "actions": [],
        "errors": [],
        "events_read": 0,
    }
    if not is_acp_available():
        result["ok"] = False
        result["errors"].append("acp-cli indisponible")
        return result

    cfg = _load_config()
    chain_id = str(cfg.get("chain_id") or "8453")
    action_types = {str(t).lower() for t in (cfg.get("action_event_types") or [])}
    path = (events_file or "").strip() or default_events_file()

    events, _ = drain_events_file(path)
    result["events_read"] = len(events)
    state = _load_state()
    seen: set[str] = set(state.get("seen_jobs") or [])

    job_ids: list[str] = []
    for ev in events:
        job_id = _extract_job_id(ev)
        if not job_id:
            continue
        ev_type = _event_type(ev)
        if action_types and ev_type and ev_type not in action_types:
            continue
        if job_id not in seen:
            job_ids.append(job_id)

    for job_id in job_ids:
        action = await _process_job(job_id, chain_id=chain_id)
        seen.add(job_id)
        if action:
            result["processed"] += 1
            result["actions"].append(action)

    state["seen_jobs"] = list(seen)
    _save_state(state)
    return result