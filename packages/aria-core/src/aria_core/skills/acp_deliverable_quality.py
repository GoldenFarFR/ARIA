"""ACP deliverable quality gates — schema + content validation before submit."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.skills.acp_offering_skill import load_offering_templates

logger = logging.getLogger(__name__)

_QUALITY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_quality.yaml"
_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass
class QualityReport:
    workflow: str
    passed: bool
    score: int
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "passed": self.passed,
            "score": self.score,
            "issues": self.issues,
            "warnings": self.warnings,
        }


@lru_cache(maxsize=1)
def _quality_doc() -> dict[str, Any]:
    if not _QUALITY_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_QUALITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def quality_gate_config(workflow: str) -> dict[str, Any]:
    gates = (_quality_doc().get("gates") or {}) if _quality_doc() else {}
    return gates.get(workflow) or {}


def submit_policy() -> dict[str, Any]:
    return (_quality_doc().get("submit") or {}) if _quality_doc() else {}


def resolve_workflow_key(offering_name: str) -> str:
    name = (offering_name or "").strip().lower()
    if "veille" in name or "zhc" in name:
        return "veille_zhc_x1"
    if "full" in name or "audit" in name:
        return "analyse_full_x1"
    if "lite" in name or "scan" in name:
        return "analyse_lite_x1"
    return "analyse_lite_x1"


def _required_fields_from_template(workflow: str) -> list[str]:
    tpl = (load_offering_templates().get(workflow) or {}).get("deliverable") or {}
    req = tpl.get("required")
    if isinstance(req, list):
        return [str(f) for f in req]
    return []


def _has_section(text: str, section: str) -> bool:
    return section.lower() in (text or "").lower()


def validate_deliverable(
    workflow: str,
    deliverable: dict[str, Any],
    *,
    onchain_score: int | None = None,
) -> QualityReport:
    """Validate deliverable against SSOT template + quality gates."""
    issues: list[str] = []
    warnings: list[str] = []
    score = 100
    cfg = quality_gate_config(workflow)
    min_score = int(cfg.get("min_quality_score") or 70)

    if not isinstance(deliverable, dict) or not deliverable:
        return QualityReport(workflow=workflow, passed=False, score=0, issues=["deliverable vide"])

    for field_name in _required_fields_from_template(workflow):
        val = deliverable.get(field_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            issues.append(f"champ requis manquant : {field_name}")
            score -= 25

    if workflow == "analyse_lite_x1":
        verdict = str(deliverable.get("liteVerdict") or "").strip().upper()
        allowed = {v.upper() for v in (cfg.get("allowed_verdicts") or ["SAFE", "CAUTION", "DANGER"])}
        if verdict not in allowed:
            issues.append(f"liteVerdict invalide : {verdict!r}")
            score -= 20
        alerts = str(deliverable.get("riskAlerts") or "").strip()
        min_chars = int(cfg.get("min_risk_alerts_chars") or 40)
        if len(alerts) < min_chars:
            issues.append(f"riskAlerts trop court ({len(alerts)} < {min_chars})")
            score -= 15
        if onchain_score is not None and onchain_score < 30 and verdict == "SAFE":
            issues.append("SAFE incompatible avec score on-chain < 30")
            score -= 20

    elif workflow == "analyse_full_x1":
        verdict = str(deliverable.get("verdict") or "").strip().upper()
        allowed = {v.upper() for v in (cfg.get("allowed_verdicts") or ["AVOID", "SPECULATIVE", "SAFE"])}
        if verdict not in allowed:
            issues.append(f"verdict invalide : {verdict!r}")
            score -= 20
        raw_score = str(deliverable.get("securityScore") or "").strip()
        if not raw_score.isdigit() or not (0 <= int(raw_score) <= 100):
            issues.append(f"securityScore invalide : {raw_score!r}")
            score -= 15
        report = str(deliverable.get("auditReport") or "").strip()
        min_chars = int(cfg.get("min_audit_report_chars") or 400)
        if len(report) < min_chars:
            issues.append(f"auditReport trop court ({len(report)} < {min_chars})")
            score -= 20
        for section in cfg.get("required_report_sections") or []:
            if not _has_section(report, str(section)):
                issues.append(f"section manquante : {section}")
                score -= 8
        if "not financial advice" not in report.lower() and "pas un conseil" not in report.lower():
            warnings.append("disclaimer absent du rapport")
            score -= 3

    elif workflow == "veille_zhc_x1":
        signal = str(deliverable.get("signal") or "").strip().upper()
        allowed = {v.upper() for v in (cfg.get("allowed_signals") or ["WATCH", "ALERT", "CLEAR"])}
        if signal not in allowed:
            issues.append(f"signal invalide : {signal!r}")
            score -= 20
        summary = str(deliverable.get("summary") or "").strip()
        if len(summary) < int(cfg.get("min_summary_chars") or 80):
            issues.append(f"summary trop court ({len(summary)})")
            score -= 15
        action = str(deliverable.get("actionNote") or "").strip()
        if len(action) < int(cfg.get("min_action_note_chars") or 40):
            issues.append(f"actionNote trop court ({len(action)})")
            score -= 15

    score = max(0, min(100, score))
    passed = score >= min_score and not issues
    return QualityReport(workflow=workflow, passed=passed, score=score, issues=issues, warnings=warnings)


def receipts_path() -> Path:
    try:
        from aria_core.paths import memory_dir

        path = memory_dir() / "acp_quality_receipts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        pass
    raw = (os.environ.get("DATA_DIR") or "").strip()
    base = Path(os.path.expandvars(raw)) if raw else Path(os.environ.get("LOCALAPPDATA", ".")) / "GoldenFar"
    path = base / "memory" / "acp_quality_receipts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_quality_receipt(
    *,
    job_id: str,
    workflow: str,
    report: QualityReport,
    submitted: bool,
    offering: str = "",
) -> None:
    if not submit_policy().get("log_receipts", True):
        return
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "offering": offering,
        "workflow": workflow,
        "submitted": submitted,
        **report.to_dict(),
    }
    path = receipts_path()
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("quality receipt log failed: %s", exc)


def should_block_submit(report: QualityReport) -> bool:
    policy = submit_policy()
    if not policy.get("block_on_quality_fail", True):
        return False
    return not report.passed