"""Skill /calibrate — entraînement épistémique opérateur."""

from __future__ import annotations

from aria_core.knowledge.calibration_ledger import format_stats_summary, record_calibration
from aria_core.knowledge.canonical_promotion import format_pending_promotion, queue_promotion
from aria_core.knowledge.contradiction import check_contradiction
from aria_core.knowledge.memory_triage import triaged_add_knowledge
from aria_core.memory import append_memory


async def execute_calibrate(body: str, lang: str = "fr") -> tuple[str, dict]:
    """
    Format: <affirmation> | vrai|faux|incertain | [source]
    """
    if "|" not in body:
        hint = (
            "Usage : /calibrate <affirmation> | vrai|faux|incertain | [source]\n"
            "Exemple : /calibrate DEXPulse est une filiale | vrai | holding"
            if lang == "fr"
            else "Usage: /calibrate <claim> | true|false|uncertain | [source]"
        )
        return hint, {"ok": False}

    parts = [p.strip() for p in body.split("|")]
    claim = parts[0]
    verdict = parts[1] if len(parts) > 1 else "incertain"
    source = parts[2] if len(parts) > 2 else "operator"

    if not claim:
        return "Affirmation requise.", {"ok": False}

    conflict, conflict_msg = check_contradiction(claim, lang)
    if conflict:
        msg = (
            f"⚠️ {conflict_msg}\nCalibration enregistrée quand même pour apprentissage."
            if lang == "fr"
            else f"⚠️ {conflict_msg}\nCalibration still logged for learning."
        )
    else:
        msg = ""

    cal = record_calibration(claim, verdict, source=source, note="telegram /calibrate")
    p_map = {"vrai": 0.95, "faux": 0.05, "incertain": 0.5}
    p_true = p_map.get(cal["verdict"], 0.5)

    promo = None
    if cal["verdict"] == "vrai" and p_true >= 0.9:
        promo = queue_promotion(claim, source=source, p_true=p_true, verdict="vrai")

    item, triage_result = await triaged_add_knowledge(
        source="calibrate",
        topic="epistemic",
        content=f"[{cal['verdict']}] {claim} (source: {source})",
        confidence=p_true,
        approved=True,
        skip_triage=True,
    )

    append_memory("epistemic", f"[calibrate] {claim[:80]} → {cal['verdict']}")

    stats = format_stats_summary(lang)
    lines = []
    if msg:
        lines.append(msg)
    if lang == "fr":
        lines.append(f"Calibration enregistrée ✅ [{cal['id']}]")
        lines.append(f"Affirmation : {claim}")
        lines.append(f"Verdict : {cal['verdict']}")
        if cal.get("brier") is not None:
            lines.append(f"Brier : {cal['brier']:.4f}")
        lines.append(stats)
    else:
        lines.append(f"Calibration saved ✅ [{cal['id']}]")
        lines.append(f"Claim: {claim}")
        lines.append(f"Verdict: {cal['verdict']}")
        if cal.get("brier") is not None:
            lines.append(f"Brier: {cal['brier']:.4f}")
        lines.append(stats)

    if promo:
        lines.append("")
        lines.append(format_pending_promotion(promo, lang))

    return "\n".join(lines), {
        "ok": True,
        "calibration": cal,
        "knowledge_id": item.id if item else None,
        "promotion": promo,
    }