from aria_core.llm_economy import (
    LlmDepth,
    calibrated_action_label,
    detect_depth,
    fallback_notice_line,
    provider_display_name,
    resolve_budget,
    skill_output_readable,
)


def test_detect_depth_brief_on_ok(monkeypatch):
    monkeypatch.setattr("aria_core.llm_economy._chiron_mode", lambda: False)
    assert detect_depth("ok prevu") == LlmDepth.BRIEF


def test_detect_depth_chiron_salut_is_develop(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="virtuals",
            aria_spark_aggressive=True,
            aria_llm_depth_default="develop",
        ),
    )
    assert detect_depth("salut") == LlmDepth.DEVELOP


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
            aria_llm_context_max_brief=3500,
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


def test_develop_enhance_budget_not_too_low(tmp_path):
    # Incident réel (12/07) : enhance_max_tokens=1200 (spark_boost) coupait en
    # plein mot les réponses "enhance" (reformulation d'une sortie de skill) en
    # profondeur develop -- confirmé par les logs prod (finish_reason=length,
    # output_tokens=1200 pile sur le plafond), littéral, jamais paramétré par
    # ARIA_LLM_MAX_TOKENS_DEVELOP (piège découvert en traçant le mauvais chemin
    # de code en premier). Verrouille un budget avec une vraie marge.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="virtuals",
            aria_spark_aggressive=True,
        ),
    )
    budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert budget.enhance_max_tokens >= 2500


def test_calibrated_label_neutral():
    label = calibrated_action_label({"groq_calibrated": True}, lang="fr")
    assert "calibré" in label.lower() or "LLM" in label
    assert "Groq calibrated" not in label


def test_skill_output_readable_short():
    assert skill_output_readable("Résultat court et lisible.")
    assert not skill_output_readable("x" * 600)


def test_provider_display_name_explicit_override():
    # #135 : signaler le fallback réellement utilisé pour ce tour, pas le provider primaire
    # de settings.llm_provider -- la surcharge explicite doit primer.
    assert provider_display_name("groq") == "Groq"
    assert provider_display_name("grok") == "Grok/xAI"
    assert provider_display_name("virtuals") == "Virtuals Spark"
    assert provider_display_name("something-unknown") == "something-unknown"


def test_fallback_notice_line_names_provider_and_sober_tone():
    line = fallback_notice_line("groq", lang="fr")
    assert "Groq" in line
    assert "Spark" in line
    assert "—" in line  # tiret cadratin toléré ici (surface opérateur, pas client -- #135 pt.3)
    assert "!" not in line  # non-alarmiste

    line_en = fallback_notice_line("grok", lang="en")
    assert "Grok/xAI" in line_en