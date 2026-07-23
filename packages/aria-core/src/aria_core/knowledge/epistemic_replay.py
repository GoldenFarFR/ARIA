"""Epistemic replay — re-verifies uncertain answers from ongoing monitoring."""

from __future__ import annotations

from aria_core.knowledge.calibration_ledger import get_uncertain_for_replay, record_prediction
from aria_core.knowledge.web_verify import web_enhance_calibrated
from aria_core.memory import append_memory


async def run_epistemic_replay(limit: int = 3) -> dict:
    pending = get_uncertain_for_replay(limit=limit)
    if not pending:
        return {"status": "idle", "replayed": 0}

    replayed = 0
    notes: list[str] = []
    for pred in pending:
        q = pred.get("question", "")
        meta = {
            "p_true": float(pred.get("p_true", 0.4)),
            "p_false": float(pred.get("p_false", 0.6)),
            "truth": "INCERTAIN",
            "groq_calibrated": True,
        }
        new_reply, new_meta = await web_enhance_calibrated(q, pred.get("reply"), meta, "fr")
        if new_meta.get("web_verified"):
            replayed += 1
            p = float(new_meta.get("p_true", 0))
            notes.append(f"{q[:40]}… → P(vrai)={p:.2f}")
            record_prediction(
                q,
                new_reply or "",
                p_true=p,
                p_false=float(new_meta.get("p_false", 0)),
                truth=str(new_meta.get("truth", "uncertain")),
                source="epistemic_replay",
                web_verified=True,
            )

    if notes:
        append_memory("epistemic", f"[replay] {replayed} revérifié(s): " + "; ".join(notes[:3]))

    return {"status": "ok", "replayed": replayed, "notes": notes}