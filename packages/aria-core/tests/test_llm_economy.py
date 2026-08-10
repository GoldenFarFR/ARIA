from aria_core.llm_economy import (
    LlmDepth,
    anthropic_depth_override,
    anthropic_routing_enabled,
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
    # #118, 27/07 -- aria_llm_model_brief is legacy, no longer surfaced as an
    # override; dormant by default (see the dedicated #118 tests below).
    assert budget.model_override is None


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


# ── #118 (27/07) -- rebuilt SSOT: replaces the #201 ARIA_LLM_MODEL_<DEPTH> /
# Virtuals-catalog mechanism (confirmed broken in prod: its guard rejected any
# operator value that numerically matched the old catalog default, even a real
# Anthropic model ID -- ARIA_LLM_MODEL_DEVELOP was silently inert). The new
# mechanism never reads a free-form provider string from .env: it hardcodes
# Haiku (trading + brief/standard) and Sonnet (develop), dormant behind
# ARIA_LLM_ANTHROPIC_ROUTING_ENABLED (off by default, as long as
# OpenRouter/Grok remain the active providers -- operator decision).

def test_anthropic_routing_disabled_by_default():
    assert anthropic_routing_enabled() is False
    assert anthropic_depth_override(LlmDepth.BRIEF) == (None, None)
    assert anthropic_depth_override(LlmDepth.STANDARD) == (None, None)
    assert anthropic_depth_override(LlmDepth.DEVELOP) == (None, None)


def test_model_override_dormant_by_default_regardless_of_legacy_settings(tmp_path):
    # Even with the legacy ARIA_LLM_MODEL_<DEPTH> settings still populated
    # (untouched .env from before #118), resolve_budget must never surface them
    # as an override while the new gate is off -- zero behavior change.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_model_standard="x-ai-grok-4-3",
            aria_llm_model_develop="anthropic-claude-opus-4-8",
            aria_llm_model_brief="deepseek-deepseek-v4-flash",
        ),
    )
    for depth in (LlmDepth.BRIEF, LlmDepth.STANDARD, LlmDepth.DEVELOP):
        budget = resolve_budget(depth, public=False)
        assert budget.model_override is None
        assert budget.model_provider_override is None


def test_anthropic_routing_maps_haiku_for_every_depth_when_enabled(tmp_path):
    # 10/08, explicit operator decision: non-trading callers (everything
    # resolve_budget serves -- conversation, /vc, smart_money, Telegram)
    # NEVER escalate to Sonnet, regardless of depth. A long/"développe"
    # message must stay on Haiku just like a brief one.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_anthropic_routing_enabled=True,
        ),
    )
    for depth in (LlmDepth.BRIEF, LlmDepth.STANDARD, LlmDepth.DEVELOP):
        budget = resolve_budget(depth, public=False)
        assert budget.model_provider_override == "anthropic"
        assert budget.model_override == "claude-haiku-4-5-20251001"


def test_anthropic_routing_trading_gates_always_haiku_never_sonnet(tmp_path):
    # 10/08, explicit operator correction: momentum_entry.py's 3 fast
    # BUY/HOLD/REJECT gates (trading=True) are NOT the "complex trading
    # decision" -- they run ~20x every 15-20 min and must stay on Haiku,
    # regardless of depth. Sonnet is reserved for vc_final_judge only.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_anthropic_routing_trading_enabled=True,
        ),
    )
    for depth in (LlmDepth.BRIEF, LlmDepth.STANDARD, LlmDepth.DEVELOP):
        provider, model = anthropic_depth_override(depth, trading=True)
        assert provider == "anthropic"
        assert model == "claude-haiku-4-5-20251001"


def test_anthropic_routing_trading_gate_alone_never_grants_vc_judge_sonnet(tmp_path):
    # The trading gate being on must never leak Sonnet access to a
    # vc_final_judge call -- two fully independent gates.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_anthropic_routing_trading_enabled=True,
            aria_llm_anthropic_routing_vc_judge_enabled=False,
        ),
    )
    assert anthropic_depth_override(LlmDepth.DEVELOP, vc_final_judge=True) == (None, None)


def test_anthropic_routing_vc_judge_maps_sonnet_when_enabled(tmp_path):
    # The ONE real caller allowed to reach Sonnet, per the operator ("de
    # toute facon la decision de trading se fait seulement sur vc pour le
    # choix final") -- off by default, dedicated gate, activation deferred.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_anthropic_routing_vc_judge_enabled=True,
        ),
    )
    provider, model = anthropic_depth_override(LlmDepth.DEVELOP, vc_final_judge=True)
    assert provider == "anthropic"
    assert model == "claude-sonnet-5"


def test_anthropic_routing_vc_judge_dormant_by_default():
    assert anthropic_depth_override(LlmDepth.DEVELOP, vc_final_judge=True) == (None, None)


def test_self_context_model_override_uses_same_ssot(tmp_path):
    # self_context (repertoire/skills internes) doit rester sur la MÊME SSOT
    # partagée, jamais une copie divergente.
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(
        data_dir=tmp_path / "data",
        settings=AriaRuntimeSettings(
            llm_provider="grok",
            aria_llm_anthropic_routing_enabled=True,
        ),
    )
    budget = resolve_budget(LlmDepth.STANDARD, public=False, self_context=True)
    assert budget.model_provider_override == "anthropic"
    assert budget.model_override == "claude-haiku-4-5-20251001"


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