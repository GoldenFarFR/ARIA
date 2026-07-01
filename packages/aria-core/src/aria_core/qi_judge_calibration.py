"""Calibration juge QI — comparaison ouvrier vs ARIA shadow (promotion auto_judge_aria)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from aria_core.capability_levels import CATEGORY_ORDER, HANDCRAFTED_MAX
from aria_core.paths import data_dir

CALIBRATION_PATH = data_dir() / "qi_judge_calibration.json"
PROMOTION_AGREEMENT_THRESHOLD = 0.9
PROMOTION_MIN_RUNS = 14
PROMOTION_WINDOW_DAYS = 30
MAX_RUNS = 200


def _load() -> dict[str, Any]:
    if not CALIBRATION_PATH.exists():
        return {"runs": [], "stats": {}}
    try:
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"runs": [], "stats": {}}


def _save(data: dict[str, Any]) -> None:
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def record_shadow_run(
    official: dict[str, tuple[int, str]],
    shadow: dict[str, tuple[int, str]],
    *,
    official_source: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare niveaux ouvrier/déterministe vs shadow LLM par axe."""
    axes: dict[str, Any] = {}
    agreements = 0
    compared = 0
    for cat in CATEGORY_ORDER:
        off_lvl, off_reason = official.get(cat, (0, ""))
        sh_lvl, sh_reason = shadow.get(cat, (0, ""))
        off_lvl = min(HANDCRAFTED_MAX, max(0, int(off_lvl)))
        sh_lvl = min(HANDCRAFTED_MAX, max(0, int(sh_lvl)))
        agree = off_lvl == sh_lvl
        if cat != "business" or off_lvl or sh_lvl:
            compared += 1
            if agree:
                agreements += 1
        axes[cat] = {
            "official": off_lvl,
            "shadow": sh_lvl,
            "agree": agree,
            "official_reason": off_reason[:200],
            "shadow_reason": sh_reason[:200],
        }

    rate = round(agreements / compared, 4) if compared else 0.0
    run = {
        "at": datetime.now(timezone.utc).isoformat(),
        "official_source": official_source[:80],
        "axes": axes,
        "agreement_rate": rate,
        "agreements": agreements,
        "compared": compared,
        "evidence": evidence or {},
    }
    data = _load()
    data.setdefault("runs", []).append(run)
    if len(data["runs"]) > MAX_RUNS:
        data["runs"] = data["runs"][-MAX_RUNS:]
    data["stats"] = compute_calibration_stats(data)
    _save(data)
    return run


def compute_calibration_stats(data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or _load()
    runs = data.get("runs") or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=PROMOTION_WINDOW_DAYS)
    recent = [
        r for r in runs
        if (_parse_at(str(r.get("at", ""))) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    total_axes = sum(int(r.get("compared", 0)) for r in recent)
    total_agree = sum(int(r.get("agreements", 0)) for r in recent)
    rate_30d = round(total_agree / total_axes, 4) if total_axes else None
    ready = (
        len(recent) >= PROMOTION_MIN_RUNS
        and rate_30d is not None
        and rate_30d >= PROMOTION_AGREEMENT_THRESHOLD
    )
    return {
        "total_runs": len(runs),
        "runs_30d": len(recent),
        "axes_30d": total_axes,
        "agreements_30d": total_agree,
        "agreement_rate_30d": rate_30d,
        "promotion_threshold": PROMOTION_AGREEMENT_THRESHOLD,
        "promotion_min_runs": PROMOTION_MIN_RUNS,
        "ready_for_promotion": ready,
    }


def is_aria_judge_promoted(*, force: bool | None = None) -> bool:
    """True si calibration 30j OK ou override opérateur."""
    from aria_core.runtime import settings

    if force is True:
        return True
    if force is False:
        return False
    if getattr(settings, "aria_qi_judge_force_aria", False):
        return True
    if getattr(settings, "aria_qi_judge_force_ouvrier", False):
        return False
    return bool(compute_calibration_stats().get("ready_for_promotion"))


def format_calibration_summary(lang: str = "fr") -> str:
    stats = compute_calibration_stats()
    rate = stats.get("agreement_rate_30d")
    rate_txt = f"{rate * 100:.1f}%" if rate is not None else "n/a"
    if lang == "fr":
        status = "promotion possible" if stats.get("ready_for_promotion") else "shadow (test)"
        return (
            f"Calibration juge QI : {stats['runs_30d']} runs / 30j, "
            f"accord {rate_txt} (seuil {PROMOTION_AGREEMENT_THRESHOLD * 100:.0f}%) — {status}"
        )
    status = "promotion ready" if stats.get("ready_for_promotion") else "shadow (test)"
    return (
        f"QI judge calibration: {stats['runs_30d']} runs / 30d, "
        f"agreement {rate_txt} — {status}"
    )