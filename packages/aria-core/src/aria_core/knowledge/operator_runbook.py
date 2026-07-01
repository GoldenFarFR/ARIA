"""Operator runbook — pitfalls + new PC checklist (SSOT: operator_pitfalls.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PITFALLS_PATH = Path(__file__).with_name("operator_pitfalls.yaml")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not _PITFALLS_PATH.exists():
        return {}
    return yaml.safe_load(_PITFALLS_PATH.read_text(encoding="utf-8")) or {}


def wants_operator_runbook(message: str) -> bool:
    lower = message.lower()
    return bool(
        re.search(
            r"runbook|nouveau\s+pc|new\s+pc|nouveau\s+github|new\s+github|"
            r"nouvel\s+agent|new\s+agent|check-aria|setup\s+render|"
            r"erreurs?\s+(?:oublie|oubli)|pitfalls?|lecons?|lessons?\s+learned|"
            r"comment\s+(?:setup|configurer)|ne\s+pas\s+oublier",
            lower,
        )
    )


def append_pitfall_if_new(pitfall: dict[str, Any]) -> bool:
    """Ajoute un pitfall si l'id n'existe pas encore (incident auto-file)."""
    pitfall_id = str(pitfall.get("id") or "").strip()
    if not pitfall_id:
        return False
    data = _load()
    pitfalls: list[dict[str, Any]] = list(data.get("pitfalls") or [])
    if any(str(p.get("id")) == pitfall_id for p in pitfalls):
        _load.cache_clear()
        return False
    entry = {
        "id": pitfall_id,
        "severity": pitfall.get("severity", "medium"),
        "lesson": str(pitfall.get("lesson", "")).strip(),
        "fix": str(pitfall.get("fix", "")).strip(),
        "verify": str(pitfall.get("verify", "")).strip(),
        "never": str(pitfall.get("never", "")).strip(),
    }
    pitfalls.append(entry)
    data["pitfalls"] = pitfalls
    _PITFALLS_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _load.cache_clear()
    return True


def format_operator_runbook(lang: str = "fr") -> str:
    data = _load()
    pitfalls = data.get("pitfalls") or []
    checklist = data.get("new_pc_checklist") or []
    repos = data.get("repos_ssot") or {}

    if lang == "fr" or (lang or "").startswith("fr"):
        lines = [
            "Runbook operateur ARIA — lecons incidents reels",
            "",
            "Pièges critiques (ne jamais reproduire) :",
        ]
        for p in pitfalls:
            sev = p.get("severity", "?")
            lines.append(f"• [{sev}] {p.get('id', '?')}")
            lines.append(f"  Lecon : {str(p.get('lesson', '')).strip()}")
            lines.append(f"  Fix : {str(p.get('fix', '')).strip()}")
            if p.get("never"):
                lines.append(f"  Interdit : {p['never']}")
            lines.append("")
        lines.append("Nouveau PC / nouveau repo GitHub :")
        for step in checklist:
            lines.append(f"  {step}")
        lines.append("")
        lines.append("Repos SSOT :")
        for k, v in repos.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append(
            "Audit rapide : cd aria-vanguard/operator && .\\check-aria-status.ps1"
        )
        lines.append("Apres modif secrets : .\\sync-render.ps1 (redeploy inclus)")
        return "\n".join(lines)

    lines = ["ARIA operator runbook — real incident lessons", ""]
    for p in pitfalls:
        lines.append(f"• {p.get('id')}: {str(p.get('lesson', '')).strip()}")
    lines.append("")
    lines.append("New PC checklist:")
    for step in checklist:
        lines.append(f"  {step}")
    return "\n".join(lines)