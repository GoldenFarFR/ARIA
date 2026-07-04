from aria_core.llm_economy import (
    LlmDepth,
    calibrated_action_label,
    detect_depth,
    resolve_budget,
    skill_output_readable,
)


def test_detect_depth_brief_on_ok():
    assert detect_depth("ok prevu") == LlmDepth.BRIEF


def test_detect_depth_develop_on_explicit():
    assert detect_depth("développe la stratégie token") == LlmDepth.DEVELOP


def test_detect_depth_override_command():
    assert detect_depth("salut /depth develop") == LlmDepth.DEVELOP


def test_detect_depth_long_message_develop():
    text = "x" * 500
    assert detect_depth(text) == LlmDepth.DEVELOP


def test_brief_budget_uses_mini_model_and_small_context(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_llm_model_brief="grok-3-mini",
            aria_llm_max_tokens_brief=180,
        ),
    )
    budget = resolve_budget(LlmDepth.BRIEF, public=False)
    assert budget.max_tokens <= 200
    assert budget.context_max_chars <= 4000
    assert budget.history_turns == 3
    assert budget.include_context_conversations is False
    assert budget.model_override == "grok-3-mini"


def test_develop_budget_full_context(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_spark_aggressive=False,
        ),
    )
    budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert budget.max_tokens >= 700
    assert budget.include_context_extras is True
    assert budget.history_turns == 10


def test_calibrated_label_neutral():
    label = calibrated_action_label({"groq_calibrated": True}, lang="fr")
    assert "calibré" in label.lower() or "LLM" in label
    assert "Groq calibrated" not in label


def test_skill_output_readable_short():
    assert skill_output_readable("Résultat court et lisible.")
    assert not skill_output_readable("x" * 600)