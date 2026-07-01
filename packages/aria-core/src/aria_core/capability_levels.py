"""Indice ARIA — niveaux 0→1000 par catégorie, objectifs progressifs (SSOT + progression)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aria_core.locale import LANG_FR
from aria_core.revenue_goals import goal_progress, total_revenue_usd
from aria_core.paths import memory_dir

RUBRIC_PATH = Path(__file__).parent / "knowledge" / "capability_rubric.yaml"
PROGRESS_PATH = memory_dir() / "capability_progress.json"
HANDCRAFTED_MAX = 12
MAX_LEVEL = 1000

CATEGORY_ORDER = ("codage", "social", "intelligence", "fiabilite", "autonomie", "business")


def _load_rubric() -> dict[str, Any]:
    with RUBRIC_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _default_progress() -> dict[str, Any]:
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "categories": {cat: {"level": 0, "history": []} for cat in CATEGORY_ORDER},
    }


def load_progress() -> dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return _default_progress()
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        for cat in CATEGORY_ORDER:
            data.setdefault("categories", {}).setdefault(cat, {"level": 0, "history": []})
        return data
    except Exception:
        return _default_progress()


def save_progress(data: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def tier_for_level(level: int, lang: str = LANG_FR) -> str:
    rubric = _load_rubric()
    target = max(1, min(level, MAX_LEVEL))
    key = "fr" if lang == LANG_FR else "en"
    for tier in rubric.get("tiers", []):
        if target <= int(tier.get("max", MAX_LEVEL)):
            return tier.get(key, tier.get("en", "?"))
    return "Légende" if lang == LANG_FR else "Legend"


def _procedural_objective(category: str, level: int, lang: str) -> dict[str, str]:
    tier = tier_for_level(level, lang)
    if lang == LANG_FR:
        templates = {
            "codage": (
                f"{tier} — cadence ingénierie",
                f"{max(1, round(1.12 ** (level / 8)))} commits qualité sandbox + "
                f"{max(1, level // 40)} greffe(s) prod validée(s) sur la période.",
                _estimate_days_fr(level),
            ),
            "social": (
                f"{tier} — présence multicanal",
                f"{max(5, round(1.1 ** (level / 10)))} publications X/Telegram factuelles "
                f"et {max(1, level // 25)} campagnes narrative cohérentes.",
                _estimate_days_fr(level),
            ),
            "intelligence": (
                f"{tier} — arbitrage stratégique",
                f"{max(3, level // 15)} décisions fondateur correctes documentées "
                f"+ roadmap {max(1, level // 50)} trimestre(s) exécutée(s).",
                _estimate_days_fr(level),
            ),
            "fiabilite": (
                f"{tier} — vérité opérationnelle",
                f"{max(30, level * 2)} jours sans incident factuel public "
                f"+ {max(10, level // 5)} entrées truth ledger vérifiées.",
                _estimate_days_fr(level),
            ),
            "autonomie": (
                f"{tier} — autonomie ZHC",
                f"{max(5, level // 10)} initiatives bout-en-bout sans relance "
                f"+ {max(1, level // 80)} priorité opérateur livrée seule.",
                _estimate_days_fr(level),
            ),
            "business": (
                f"{tier} — moat économique",
                f"{_business_target(level):,} $ revenu réel cumulé ou "
                f"{_business_monthly_target(level):,} $/mois logués — produit avant token.",
                _estimate_days_fr(level),
            ),
        }
        title, objective, days = templates.get(category, ("Objectif", "Progression", "?"))
        return {"objective": objective, "days": days, "title": title}
    templates_en = {
        "codage": (
            f"{tier} — engineering cadence",
            f"{max(1, round(1.12 ** (level / 8)))} quality sandbox commits + "
            f"{max(1, level // 40)} validated prod graft(s) in period.",
            _estimate_days_en(level),
        ),
        "social": (
            f"{tier} — multi-channel presence",
            f"{max(5, round(1.1 ** (level / 10)))} factual X/Telegram posts "
            f"and {max(1, level // 25)} coherent narrative campaigns.",
            _estimate_days_en(level),
        ),
        "intelligence": (
            f"{tier} — strategic arbitration",
            f"{max(3, level // 15)} correct documented founder decisions "
            f"+ {max(1, level // 50)} executed quarterly roadmap(s).",
            _estimate_days_en(level),
        ),
        "fiabilite": (
            f"{tier} — operational truth",
            f"{max(30, level * 2)} days without public factual incident "
            f"+ {max(10, level // 5)} verified truth ledger entries.",
            _estimate_days_en(level),
        ),
        "autonomie": (
            f"{tier} — ZHC autonomy",
            f"{max(5, level // 10)} end-to-end initiatives without nudging "
            f"+ {max(1, level // 80)} operator priority delivered solo.",
            _estimate_days_en(level),
        ),
        "business": (
            f"{tier} — economic moat",
            f"${_business_target(level):,} cumulative real revenue or "
            f"${_business_monthly_target(level):,}/mo logged — product before token.",
            _estimate_days_en(level),
        ),
    }
    title, objective, days = templates_en.get(category, ("Goal", "Progress", "?"))
    return {"objective": objective, "days": days, "title": title}


def _business_target(level: int) -> int:
    return max(10, int(10 * (1.14 ** (level / 12))))


def _business_monthly_target(level: int) -> int:
    return max(50, int(50 * (1.1 ** (level / 20))))


def _estimate_days_fr(level: int) -> str:
    days = _estimate_days_numeric(level)
    if days < 1:
        return "< 1 jour"
    if days < 7:
        return f"{int(days)} jours"
    if days < 30:
        return f"{int(days / 7)} semaines"
    if days < 365:
        return f"{int(days / 30)} mois"
    years = days / 365
    if years < 2:
        return "1 an"
    return f"{int(years)} ans"


def _estimate_days_en(level: int) -> str:
    days = _estimate_days_numeric(level)
    if days < 1:
        return "< 1 day"
    if days < 7:
        return f"{int(days)} days"
    if days < 30:
        return f"{int(days / 7)} weeks"
    if days < 365:
        return f"{int(days / 30)} months"
    years = days / 365
    if years < 2:
        return "1 year"
    return f"{int(years)} years"


def _estimate_days_numeric(level: int) -> float:
    if level <= 5:
        return 0.3 + level * 0.15
    if level <= 20:
        return 1 + (level - 5) * 0.4
    if level <= 100:
        return 7 + (level - 20) * 0.35
    if level <= 400:
        return 35 + (level - 100) * 0.6
    if level <= 700:
        return 215 + (level - 400) * 1.2
    return 575 + (level - 700) * 3.5


def get_level_definition(category: str, level: int, lang: str = LANG_FR) -> dict[str, Any]:
    if level < 1 or level > MAX_LEVEL:
        raise ValueError(f"level must be 1..{MAX_LEVEL}")
    rubric = _load_rubric()
    cat = rubric["categories"][category]
    key = "fr" if lang == LANG_FR else "en"
    handcrafted = cat.get("levels", {}).get(level) or cat.get("levels", {}).get(str(level))
    if handcrafted:
        return {
            "level": level,
            "tier": tier_for_level(level, lang),
            "objective": handcrafted[f"objective_{key}"],
            "days": handcrafted.get(f"days_{key}", "?"),
            "metric": handcrafted.get("metric"),
            "target": handcrafted.get("target"),
            "handcrafted": True,
        }
    proc = _procedural_objective(category, level, lang)
    return {
        "level": level,
        "tier": tier_for_level(level, lang),
        "objective": proc["objective"],
        "days": proc["days"],
        "metric": _procedural_metric(category, level),
        "target": _procedural_target(category, level),
        "handcrafted": False,
    }


def _procedural_metric(category: str, level: int) -> str | None:
    if category == "business":
        return "revenue_usd_total" if level % 2 == 0 else "revenue_usd_monthly"
    return None


def _procedural_target(category: str, level: int) -> float | int | None:
    if category != "business":
        return None
    if level % 2 == 0:
        return _business_target(level)
    return _business_monthly_target(level)


def _metric_satisfied(metric: str | None, target: float | int | None) -> bool:
    if not metric or target is None:
        return False
    progress = goal_progress()
    if metric == "revenue_usd_monthly":
        return float(progress.get("monthly_total_usd", 0)) >= float(target)
    if metric == "revenue_usd_total":
        return total_revenue_usd() >= float(target)
    return False


def global_index(progress: dict[str, Any] | None = None) -> float:
    data = progress or load_progress()
    levels = [data["categories"][c]["level"] for c in CATEGORY_ORDER]
    return round(sum(levels) / len(levels), 1)


def category_state(category: str, lang: str = LANG_FR) -> dict[str, Any]:
    rubric = _load_rubric()
    progress = load_progress()
    completed = int(progress["categories"][category]["level"])
    meta = rubric["categories"][category]
    key = "fr" if lang == LANG_FR else "en"
    if completed >= MAX_LEVEL:
        return {
            "category": category,
            "label": meta[f"label_{key}"],
            "icon": meta.get("icon", ""),
            "completed_level": MAX_LEVEL,
            "next_level": None,
            "tier": tier_for_level(MAX_LEVEL, lang),
            "objective": "MAX — palier Légende atteint." if lang == LANG_FR else "MAX — Legend tier reached.",
            "days": "—",
            "auto_ready": False,
        }
    next_level = completed + 1
    definition = get_level_definition(category, next_level, lang)
    return {
        "category": category,
        "label": meta[f"label_{key}"],
        "icon": meta.get("icon", ""),
        "completed_level": completed,
        "next_level": next_level,
        "tier": definition["tier"],
        "objective": definition["objective"],
        "days": definition["days"],
        "auto_ready": _metric_satisfied(definition.get("metric"), definition.get("target")),
    }


def full_status(lang: str = LANG_FR) -> dict[str, Any]:
    progress = load_progress()
    categories = {cat: category_state(cat, lang) for cat in CATEGORY_ORDER}
    return {
        "global_index": global_index(progress),
        "max_level": MAX_LEVEL,
        "started_at": progress.get("started_at"),
        "categories": categories,
    }


def complete_level(
    category: str,
    *,
    note: str = "",
    source: str = "operator",
) -> dict[str, Any]:
    if category not in CATEGORY_ORDER:
        return {"ok": False, "error": "unknown_category"}
    progress = load_progress()
    cat = progress["categories"][category]
    completed = int(cat["level"])
    if completed >= MAX_LEVEL:
        return {"ok": False, "error": "max_level", "level": completed}
    next_level = completed + 1
    cat["level"] = next_level
    cat.setdefault("history", []).append({
        "level": next_level,
        "at": datetime.now(timezone.utc).isoformat(),
        "note": note[:300],
        "source": source,
    })
    save_progress(progress)
    return {
        "ok": True,
        "category": category,
        "new_level": next_level,
        "global_index": global_index(progress),
    }


def check_auto_completions() -> list[dict[str, Any]]:
    """Auto-level business when revenue metrics are met."""
    completed_events: list[dict[str, Any]] = []
    for _ in range(20):
        progressed = False
        for category in CATEGORY_ORDER:
            state = category_state(category, LANG_FR)
            if state.get("auto_ready") and state.get("next_level"):
                result = complete_level(
                    category,
                    note=f"auto: {state['objective'][:120]}",
                    source="auto",
                )
                if result.get("ok"):
                    completed_events.append(result)
                    progressed = True
        if not progressed:
            break
    return completed_events


def format_summary(lang: str = LANG_FR) -> str:
    status = full_status(lang)
    idx = status["global_index"]
    max_lv = status["max_level"]
    if lang == LANG_FR:
        lines = [
            f"Indice ARIA : {idx} / {max_lv}",
            f"(moyenne des 6 axes — départ {status.get('started_at', '')[:10]})",
            "",
        ]
        for cat in CATEGORY_ORDER:
            s = status["categories"][cat]
            if s["next_level"]:
                lines.append(
                    f"{s['icon']} {s['label']} : {s['completed_level']} → nv {s['next_level']} "
                    f"[{s['tier']}]"
                )
                lines.append(f"   Objectif : {s['objective']}")
                lines.append(f"   Estimé : {s['days']}")
                if s["auto_ready"]:
                    lines.append("   ✅ Métrique atteinte — /level up " + cat)
            else:
                lines.append(f"{s['icon']} {s['label']} : {MAX_LEVEL} MAX [Légende]")
            lines.append("")
        lines.append("Valider : /level up <codage|social|intelligence|fiabilite|autonomie|business>")
        return "\n".join(lines).strip()
    lines = [
        f"ARIA Index: {idx} / {max_lv}",
        f"(average of 6 axes — started {status.get('started_at', '')[:10]})",
        "",
    ]
    for cat in CATEGORY_ORDER:
        s = status["categories"][cat]
        if s["next_level"]:
            lines.append(
                f"{s['icon']} {s['label']}: {s['completed_level']} → lvl {s['next_level']} "
                f"[{s['tier']}]"
            )
            lines.append(f"   Goal: {s['objective']}")
            lines.append(f"   Est.: {s['days']}")
            if s["auto_ready"]:
                lines.append("   ✅ Metric met — /level up " + cat)
        else:
            lines.append(f"{s['icon']} {s['label']}: {MAX_LEVEL} MAX [Legend]")
        lines.append("")
    lines.append("Validate: /level up <codage|social|intelligence|fiabilite|autonomie|business>")
    return "\n".join(lines).strip()


def years_to_max_estimate() -> float:
    """Rough cumulative years to reach 1000 on all axes (illustrative)."""
    total_days = sum(_estimate_days_numeric(lv) for lv in range(1, MAX_LEVEL + 1))
    return round(total_days / 365, 1)