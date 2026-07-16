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


def test_standard_model_override_honored_off_virtuals_when_explicitly_configured(tmp_path):
    # #201 : dès que Groq/DeepSeek devient le provider actif, standard/develop
    # doivent quand même différer si l'opérateur a configuré un vrai modèle pour
    # CE provider -- avant le correctif, _spark_model_for_depth() était gaté
    # _spark_active() et renvoyait toujours None hors Virtuals.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_llm_model_standard="llama-3.3-70b-versatile",
        ),
    )
    budget = resolve_budget(LlmDepth.STANDARD, public=False)
    assert budget.model_override == "llama-3.3-70b-versatile"


def test_develop_model_override_honored_off_virtuals_when_explicitly_configured(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="deepseek",
            aria_llm_model_develop="deepseek-reasoner",
        ),
    )
    budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert budget.model_override == "deepseek-reasoner"


def test_standard_and_develop_differ_off_virtuals_when_both_configured(tmp_path):
    # Le coeur du bug #201 : standard ET develop utilisaient le MEME modele (aucun
    # override, silence complet) des que le provider actif n'etait pas "virtuals".
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_llm_model_standard="llama-3.3-70b-versatile",
            aria_llm_model_develop="llama-3.3-70b-specdec",
        ),
    )
    std_budget = resolve_budget(LlmDepth.STANDARD, public=False)
    dev_budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert std_budget.model_override != dev_budget.model_override
    assert std_budget.model_override == "llama-3.3-70b-versatile"
    assert dev_budget.model_override == "llama-3.3-70b-specdec"


def test_standard_model_override_none_off_virtuals_when_still_catalog_default(tmp_path):
    # Garde-fou #201 : si l'opérateur n'a JAMAIS surchargé aria_llm_model_standard
    # pour le nouveau provider (valeur encore le défaut catalogue Virtuals
    # "x-ai-grok-4-3"), ne jamais l'envoyer tel quel à une vraie API tierce --
    # mieux vaut aucun override (le provider résout son propre défaut) qu'un ID
    # de modèle invalide.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_llm_model_standard="x-ai-grok-4-3",
        ),
    )
    budget = resolve_budget(LlmDepth.STANDARD, public=False)
    assert budget.model_override is None
    assert budget.model_override != "x-ai-grok-4-3"


def test_develop_model_override_none_off_virtuals_when_still_catalog_default(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="deepseek",
            aria_llm_model_develop="anthropic-claude-opus-4-8",
        ),
    )
    budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert budget.model_override is None
    assert budget.model_override != "anthropic-claude-opus-4-8"


def test_standard_and_develop_models_differ_on_virtuals(tmp_path):
    # Non-régression : le comportement historique sur Virtuals (catalogue Spark)
    # reste inchangé par le correctif #201.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="virtuals",
            aria_llm_model_standard="x-ai-grok-4-3",
            aria_llm_model_develop="anthropic-claude-opus-4-8",
        ),
    )
    std_budget = resolve_budget(LlmDepth.STANDARD, public=False)
    dev_budget = resolve_budget(LlmDepth.DEVELOP, public=False)
    assert std_budget.model_override == "x-ai-grok-4-3"
    assert dev_budget.model_override == "anthropic-claude-opus-4-8"


def test_self_context_model_override_also_fixed_off_virtuals(tmp_path):
    # self_context (repertoire/skills internes) passait par le même _brief_model_if
    # -- même bug, même correctif requis.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="groq",
            aria_llm_model_standard="llama-3.3-70b-versatile",
        ),
    )
    budget = resolve_budget(LlmDepth.STANDARD, public=False, self_context=True)
    assert budget.model_override == "llama-3.3-70b-versatile"


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