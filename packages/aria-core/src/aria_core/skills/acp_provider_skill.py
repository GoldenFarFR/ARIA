"""ACP provider — drains the JSONL events file + fulfills jobs via acp-cli (local PC)."""
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
from aria_core.skills.acp_deliverable_quality import (
    log_quality_receipt,
    should_block_submit,
    validate_deliverable,
)
from aria_core.skills.acp_schema import get_acp_strict_rules
from aria_core.skills.acp_workflow_engine import build_deliverable_for_job

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


# Backward-compatible exports for tests
def _heuristic_audit(contract: str, *, full: bool) -> dict[str, Any]:
    """Sync stub — prefer acp_workflow_engine + acp_onchain_scan."""
    import asyncio

    from aria_core.skills.acp_onchain_scan import scan_base_token
    from aria_core.skills.acp_workflow_engine import build_full_deliverable, build_lite_deliverable

    ctx = asyncio.run(scan_base_token(contract))
    return build_full_deliverable(ctx) if full else build_lite_deliverable(ctx)


def _build_deliverable(history: dict) -> dict[str, Any] | None:
    import asyncio

    offering = _offering_name(history)
    deliverable, _wf, _ctx = asyncio.run(build_deliverable_for_job(offering, history))
    return deliverable


async def _process_job(job_id: str, *, chain_id: str) -> str | None:
    history, err = job_history(job_id, chain_id=chain_id)
    if err or not history:
        logger.warning("ACP job %s history: %s", job_id, err)
        return None
    if not _needs_provider_submit(history):
        return None

    offering = _offering_name(history)
    deliverable, workflow, ctx = await build_deliverable_for_job(offering, history)
    if not deliverable:
        return None

    onchain_score = ctx.security_score if ctx else None
    report = validate_deliverable(workflow, deliverable, onchain_score=onchain_score)
    if should_block_submit(report):
        log_quality_receipt(
            job_id=job_id,
            workflow=workflow,
            report=report,
            submitted=False,
            offering=offering,
        )
        logger.warning(
            "ACP job %s quality gate FAILED (score=%s): %s",
            job_id,
            report.score,
            "; ".join(report.issues),
        )
        return f"quality_blocked:{job_id}"

    # Reminder of the strong ACP barrier: the operator must audit before final validation
    audit_note = " (audit qualité opérateur demandé avant promotion)"
    # We don't alter the deliverable itself, we just log the obligation

    ok, msg = provider_submit(job_id, deliverable, chain_id=chain_id)
    log_quality_receipt(
        job_id=job_id,
        workflow=workflow,
        report=report,
        submitted=ok,
        offering=offering,
    )
    if ok:
        try:
            from aria_core.revenue_goals import record_revenue

            cfg = _load_config()
            offerings = cfg.get("offerings") or {}
            price = 0.0
            for key, spec in offerings.items():
                if key.replace("_", "") in offering.lower().replace("_", "") or key in offering.lower():
                    price = float((spec or {}).get("price_usd") or 0)
                    break
            if price > 0:
                record_revenue(price, source="acp_provider", note=f"job:{job_id} {workflow}")
        except Exception as exc:
            logger.debug("revenue log skip: %s", exc)
        try:
            from aria_core.skills.acp_workflow_social import (
                enqueue_workflow_used_tweet,
                extract_offering_id,
                flush_workflow_used_tweet,
            )

            off_id = extract_offering_id(history)
            social = enqueue_workflow_used_tweet(
                offering_name=offering or workflow,
                workflow_key=workflow,
                job_id=job_id,
                offering_id=off_id,
            )
            if social.get("queued"):
                flush = await flush_workflow_used_tweet()
                if flush and flush.get("posted"):
                    logger.info("ACP workflow-used tweet posted for %s", offering or workflow)
                elif flush and not flush.get("posted"):
                    logger.info("ACP workflow-used tweet queued: %s", flush.get("reason"))
        except Exception as exc:
            logger.debug("workflow-used social skip: %s", exc)
        # ACP barrier: explicitly flag that operator audit is required
        return f"submit:{job_id}"
    logger.warning("ACP submit %s failed: %s", job_id, msg)
    return None


def drain_events_file(path: str) -> tuple[list[dict], int]:
    """Reads new JSONL lines since the last offset."""
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
    """Drains the events file + attempts fulfillment for each new/actionable job."""
    result: dict[str, Any] = {
        "ok": True,
        "processed": 0,
        "actions": [],
        "errors": [],
        "quality_blocked": 0,
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
            if action.startswith("quality_blocked:"):
                result["quality_blocked"] += 1
                result["actions"].append(action)
            elif action.startswith("submit:"):
                result["processed"] += 1
                result["actions"].append(action)

    state["seen_jobs"] = list(seen)
    _save_state(state)
    return result