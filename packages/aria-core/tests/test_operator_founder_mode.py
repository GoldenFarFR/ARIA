"""Mode fondateur — juste milieu shell opérateur."""

import pytest

from aria_core.llm_economy import LlmDepth, detect_depth, resolve_budget


def test_founder_default_depth_standard(tmp_path, monkeypatch):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            aria_operator_founder_mode=True,
            llm_provider="virtuals",
            aria_spark_aggressive=True,
            aria_llm_depth_default="standard",
        ),
    )
    assert detect_depth("tu a prevu quoi sur acp ?") == LlmDepth.STANDARD


def test_founder_budget_boost(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            aria_operator_founder_mode=True,
            llm_provider="groq",
            aria_llm_max_tokens_standard=700,
            aria_spark_aggressive=False,
        ),
    )
    budget = resolve_budget(LlmDepth.STANDARD, public=False)
    assert budget.max_tokens >= 700
    assert budget.include_context_conversations is True


@pytest.mark.asyncio
async def test_finalize_reply_skips_founder_gates(tmp_path):
    from aria_core.knowledge.epistemic_pipeline import finalize_reply
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(aria_operator_founder_mode=True),
    )
    reply, data = await finalize_reply(
        "test",
        "Nos revenus sont de 50000$ ce mois",
        {"groq_calibrated": True, "p_true": 0.9},
        "fr",
        public=False,
    )
    assert reply == "Nos revenus sont de 50000$ ce mois"
    assert "critic" not in data
    assert "calibration_id" not in data