"""ACP mode dégradé — préparer deliverable local sans provider submit (Hermès UI)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import memory_dir
from aria_core.skills.acp_cli import is_acp_available, job_history
from aria_core.skills.acp_deliverable_quality import validate_deliverable
from aria_core.skills.acp_workflow_engine import build_deliverable_for_job

_PREPARE_RE = re.compile(
    r"(?i)(?:"
    r"pr[ée]par(?:er|e)\s+(?:le\s+)?job\s+acp|"
    r"prepare\s+(?:acp\s+)?job|"
    r"deliverable\s+(?:job\s+)?acp|"
    r"livrable\s+(?:job\s+)?acp"
    r")"
)
_JOB_ID_RE = re.compile(r"(?i)\b(?:job[- ]?id|job)\s*[:#]?\s*(0x[a-fA-F0-9]{8,})")
_JOB_HEX_RE = re.compile(r"\b(0x[a-fA-F0-9]{8,})\b")
_OFFERING_RE = re.compile(
    r"(?i)\b(?:offre|offering|workflow|template)\s+([a-z][a-z0-9_]*)"
)
_CONTRACT_RE = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
_BRIEF_RE = re.compile(r"(?i)\bbrief\s+(.+?)(?:\s+offre|\s+offering|$)")

_PREPARED_DIR = memory_dir() / "acp_prepared"


def wants_acp_prepare(message: str) -> bool:
    return bool(_PREPARE_RE.search((message or "").strip()))


def _parse_job_id(message: str) -> str | None:
    m = _JOB_ID_RE.search(message or "")
    if m:
        return m.group(1).strip()
    hexes = _JOB_HEX_RE.findall(message or "")
    return hexes[0] if hexes else None


def _parse_offering(message: str) -> str:
    m = _OFFERING_RE.search(message or "")
    if m:
        return m.group(1).strip().lower()
    lower = (message or "").lower()
    for name in ("analyse_full_x1", "analyse_lite_x1", "veille_zhc_x1"):
        if name in lower:
            return name
    return "analyse_lite_x1"


def _parse_requirements(message: str) -> dict[str, Any]:
    req: dict[str, Any] = {}
    contracts = _CONTRACT_RE.findall(message or "")
    if contracts:
        req["contractAddress"] = contracts[-1]
    m = _BRIEF_RE.search(message or "")
    if m:
        req["brief"] = m.group(1).strip()[:500]
    sym = re.search(r"(?i)\bsymbols?\s+([A-Za-z0-9_,\s-]+)", message or "")
    if sym:
        req["symbols"] = sym.group(1).strip()[:120]
    return req


def _history_from_message(message: str, offering: str) -> dict[str, Any]:
    req = _parse_requirements(message)
    return {
        "offeringName": offering,
        "requirements": req,
        "job": {"offeringName": offering, "requirements": req},
    }


def _save_prepared(
    job_id: str,
    *,
    offering: str,
    workflow: str,
    deliverable: dict[str, Any],
    quality: dict[str, Any],
    source: str,
) -> Path:
    _PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)[:80]
    path = _PREPARED_DIR / f"{safe_id}.json"
    doc = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "offering": offering,
        "workflow": workflow,
        "source": source,
        "deliverable": deliverable,
        "quality": quality,
        "hermes_note": (
            "Mode dégradé Virtuals — coller deliverable JSON dans Hermès "
            "(provider submit CLI indisponible)."
        ),
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _format_deliverable_text(deliverable: dict[str, Any]) -> str:
    return json.dumps(deliverable, indent=2, ensure_ascii=False)


def _format_reply(
    *,
    job_id: str,
    offering: str,
    workflow: str,
    deliverable: dict[str, Any],
    quality: dict[str, Any],
    saved: Path,
    source: str,
    history_err: str | None,
    lang: str,
) -> str:
    payload = _format_deliverable_text(deliverable)
    if lang == "fr":
        lines = [
            "═══ ACP PRÉPARATION (mode dégradé) ═══",
            "",
            f"Job : {job_id}",
            f"Offre : {offering} → workflow {workflow}",
            f"Source : {source}",
            f"Qualité : score {quality.get('score', '?')} — "
            f"{'OK' if quality.get('ok') else 'a revoir'}",
        ]
        if history_err:
            lines.append(f"Historique API : indisponible ({history_err[:120]})")
        lines.extend([
            "",
            "── Deliverable JSON (copier dans Hermès) ──",
            payload,
            "",
            f"Fichier : {saved}",
            "",
            "Étapes : Hermès → job → coller JSON → soumettre manuellement.",
        ])
        return "\n".join(lines)

    lines = [
        "═══ ACP PREPARE (degraded mode) ═══",
        f"Job: {job_id} · offering: {offering} · workflow: {workflow}",
        f"Quality: {quality.get('score', '?')}",
        "",
        payload,
        "",
        f"Saved: {saved}",
    ]
    return "\n".join(lines)


async def execute_acp_prepare(message: str, lang: str = "fr") -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    if not is_acp_available():
        msg = "ACP — acp-cli introuvable." if lang_key == "fr" else "ACP — acp-cli missing."
        return msg, {"acp": "prepare_no_cli"}

    job_id = _parse_job_id(message)
    if not job_id:
        hint = (
            "Précise le job : « préparer job acp 0x… offre analyse_lite_x1 contract 0x… »"
            if lang_key == "fr"
            else "Specify: prepare job acp 0x… offering analyse_lite_x1 contract 0x…"
        )
        return hint, {"acp": "prepare_parse"}

    offering = _parse_offering(message)
    history_err: str | None = None
    history: dict[str, Any] | None = None
    source = "manual"

    hist, err = job_history(job_id)
    if hist and not err:
        history = hist
        source = "job_history"
        off_from_hist = (
            (hist.get("offeringName") or hist.get("offering_name") or "")
            if isinstance(hist, dict)
            else ""
        )
        if isinstance(off_from_hist, str) and off_from_hist.strip():
            offering = off_from_hist.strip().lower()
    else:
        history_err = (err or "historique indisponible")[:200]
        history = _history_from_message(message, offering)
        if not _parse_requirements(message) and offering in ("analyse_lite_x1", "analyse_full_x1"):
            need = (
                "Contract requis : « … contract 0x… » (API job history en panne)."
                if lang_key == "fr"
                else "Contract required — job history API unavailable."
            )
            return need, {"acp": "prepare_needs_contract", "job_id": job_id}

    deliverable, workflow, _ctx = await build_deliverable_for_job(
        offering, history or _history_from_message(message, offering)
    )
    if not deliverable:
        msg = (
            f"Impossible de construire le deliverable pour {offering}."
            if lang_key == "fr"
            else f"Cannot build deliverable for {offering}."
        )
        return msg, {"acp": "prepare_build_failed", "job_id": job_id}

    report = validate_deliverable(workflow, deliverable)
    quality = {
        "ok": report.passed,
        "score": report.score,
        "issues": list(report.issues or [])[:6],
    }
    saved = _save_prepared(
        job_id,
        offering=offering,
        workflow=workflow,
        deliverable=deliverable,
        quality=quality,
        source=source,
    )
    body = _format_reply(
        job_id=job_id,
        offering=offering,
        workflow=workflow,
        deliverable=deliverable,
        quality=quality,
        saved=saved,
        source=source,
        history_err=history_err,
        lang=lang_key,
    )
    return body, {
        "acp": "prepare",
        "job_id": job_id,
        "offering": offering,
        "workflow": workflow,
        "saved": str(saved),
        "quality_ok": quality["ok"],
        "source": source,
    }


def list_prepared_jobs() -> list[dict[str, Any]]:
    if not _PREPARED_DIR.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(_PREPARED_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc["path"] = str(path)
                rows.append(doc)
        except Exception:
            continue
    return rows[:20]