"""Pipeline épistémique — web verify, critic, calibration log, contradictions."""

from __future__ import annotations

from aria_core.knowledge.calibration_ledger import record_prediction
from aria_core.knowledge.contradiction import check_contradiction
from aria_core.knowledge.epistemic_critic import critic_check
from aria_core.knowledge.web_verify import web_enhance_calibrated


async def finalize_reply(
    question: str,
    reply: str,
    data: dict,
    lang: str,
    *,
    public: bool = False,
    skill_used: str | None = None,
) -> tuple[str, dict]:
    """Applique critic gate + log calibration sur toute réponse opérateur."""
    from aria_core.runtime import settings

    if public or not reply:
        return reply, data
    if getattr(settings, "aria_operator_founder_mode", False):
        return reply, data

    data = dict(data) if isinstance(data, dict) else {}

    skip_contradiction = skill_used == "launchpad_select" and data.get("mode") == "methodology"
    conflict, conflict_msg = (False, "") if skip_contradiction else check_contradiction(reply, lang)
    if conflict:
        note = (
            f"⚠️ Contradiction détectée : {conflict_msg}\n\n"
            if lang == "fr"
            else f"⚠️ Contradiction detected: {conflict_msg}\n\n"
        )
        reply = note + reply
        data["contradiction"] = conflict_msg

    safe, adjusted, critic_meta = await critic_check(
        reply, lang, skill_used=skill_used, data=data,
    )
    reply = adjusted
    data["critic"] = critic_meta

    p_true = float(data.get("p_true", 0))
    p_false = float(data.get("p_false", 0))
    if data.get("groq_calibrated") or data.get("groq_web_verified") or p_true > 0:
        pred_id = record_prediction(
            question,
            reply,
            p_true=p_true,
            p_false=p_false,
            truth=str(data.get("truth", "uncertain")),
            source=str(data.get("source", "reply")),
            skill=skill_used,
            web_verified=bool(data.get("web_verified")),
        )
        data["calibration_id"] = pred_id

    return reply, data


async def enhance_calibrated_answer(
    query: str,
    reply: str | None,
    meta: dict,
    lang: str,
) -> tuple[str | None, dict]:
    """Web verify + re-calibration si incertain."""
    if not reply:
        return reply, meta
    return await web_enhance_calibrated(query, reply, meta, lang)