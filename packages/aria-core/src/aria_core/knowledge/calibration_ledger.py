"""Journal de calibration — prédictions P(vrai) + corrections opérateur (score Brier)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aria_core.paths import data_dir

LEDGER_PATH = data_dir() / "calibration_ledger.json"


def _load() -> dict:
    if not LEDGER_PATH.exists():
        return {"predictions": [], "calibrations": [], "stats": {}}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"predictions": [], "calibrations": [], "stats": {}}


def _save(data: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_prediction(
    question: str,
    reply: str,
    *,
    p_true: float = 0.0,
    p_false: float = 0.0,
    truth: str = "uncertain",
    source: str = "groq",
    skill: str | None = None,
    web_verified: bool = False,
) -> str:
    entry_id = str(uuid4())[:10]
    data = _load()
    data["predictions"].append({
        "id": entry_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "question": question[:500],
        "reply": reply[:800],
        "p_true": round(p_true, 4),
        "p_false": round(p_false, 4),
        "truth": truth,
        "source": source,
        "skill": skill,
        "web_verified": web_verified,
        "resolved": None,
    })
    if len(data["predictions"]) > 500:
        data["predictions"] = data["predictions"][-500:]
    _save(data)
    return entry_id


def record_calibration(
    claim: str,
    verdict: str,
    *,
    source: str = "operator",
    note: str = "",
    prediction_id: str | None = None,
) -> dict:
    """verdict: vrai | faux | incertain"""
    v = verdict.strip().lower()
    if v not in ("vrai", "faux", "incertain", "true", "false", "uncertain"):
        v = "incertain"
    if v == "true":
        v = "vrai"
    if v == "false":
        v = "faux"
    if v == "uncertain":
        v = "incertain"

    actual = 1.0 if v == "vrai" else (0.0 if v == "faux" else 0.5)
    brier: float | None = None

    data = _load()
    if prediction_id:
        for pred in reversed(data["predictions"]):
            if pred["id"] == prediction_id and pred.get("resolved") is None:
                p = float(pred.get("p_true", 0.5))
                brier = round((p - actual) ** 2, 4)
                pred["resolved"] = v
                pred["brier"] = brier
                break

    cal = {
        "id": str(uuid4())[:10],
        "at": datetime.now(timezone.utc).isoformat(),
        "claim": claim[:500],
        "verdict": v,
        "actual": actual,
        "source": source[:120],
        "note": note[:200],
        "prediction_id": prediction_id,
        "brier": brier,
    }
    data["calibrations"].append(cal)
    if len(data["calibrations"]) > 300:
        data["calibrations"] = data["calibrations"][-300:]
    data["stats"] = compute_stats(data)
    _save(data)
    return cal


def compute_stats(data: dict | None = None) -> dict:
    data = data or _load()
    resolved = [p for p in data.get("predictions", []) if p.get("brier") is not None]
    briers = [float(p["brier"]) for p in resolved]
    avg_brier = round(sum(briers) / len(briers), 4) if briers else None
    return {
        "predictions": len(data.get("predictions", [])),
        "calibrations": len(data.get("calibrations", [])),
        "resolved": len(resolved),
        "avg_brier": avg_brier,
        "reliability_hint": (
            "excellent" if avg_brier is not None and avg_brier < 0.1
            else "bon" if avg_brier is not None and avg_brier < 0.2
            else "à améliorer" if avg_brier is not None
            else "pas encore mesuré"
        ),
    }


def get_uncertain_for_replay(limit: int = 5) -> list[dict]:
    data = _load()
    candidates = [
        p for p in data.get("predictions", [])
        if p.get("resolved") is None
        and float(p.get("p_true", 1)) < 0.65
        and not p.get("web_verified")
    ]
    return candidates[-limit:]


def format_stats_summary(lang: str = "fr") -> str:
    stats = compute_stats()
    if lang == "fr":
        brier = stats.get("avg_brier")
        brier_txt = f"{brier:.3f}" if brier is not None else "n/a"
        return (
            f"Calibration : {stats['predictions']} prédictions, "
            f"{stats['resolved']} résolues, Brier moyen {brier_txt} "
            f"({stats['reliability_hint']})"
        )
    brier = stats.get("avg_brier")
    brier_txt = f"{brier:.3f}" if brier is not None else "n/a"
    return (
        f"Calibration: {stats['predictions']} predictions, "
        f"{stats['resolved']} resolved, avg Brier {brier_txt} "
        f"({stats['reliability_hint']})"
    )