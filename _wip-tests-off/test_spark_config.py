from aria_core.spark_config import (
    BANNED_VIRTUALS_PRIMARY_MODELS,
    DEFAULT_MODEL_STANDARD,
    resolve_primary_llm_model,
    resolve_spark_runtime,
    spark_model_for_prompt,
    verify_spark_alignment,
)


def test_resolve_primary_skips_banned_deepseek(monkeypatch):
    vault = {"LLM_MODEL": "deepseek-deepseek-v4-pro", "ARIA_LLM_MODEL_STANDARD": "x-ai-grok-4-3"}
    assert resolve_primary_llm_model(vault) == "x-ai-grok-4-3"
    assert "deepseek-deepseek-v4-pro" in BANNED_VIRTUALS_PRIMARY_MODELS


def test_spark_model_for_prompt_depth():
    vault = {
        "ARIA_LLM_MODEL_DEVELOP": "x-ai-grok-4-3",
        "ARIA_LLM_MODEL_STANDARD": "x-ai-grok-4-3",
    }
    assert "grok" in spark_model_for_prompt("/depth develop plan", vault).lower()
    assert "grok" in spark_model_for_prompt("salut", vault).lower()


def test_verify_spark_alignment_structure(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "spark")
    monkeypatch.setenv("LLM_PROVIDER", "virtuals")
    monkeypatch.setenv("VIRTUALS_API_KEY", "acp-" + "x" * 20)
    monkeypatch.setenv("LLM_MODEL", DEFAULT_MODEL_STANDARD)
    checks = verify_spark_alignment()
    names = {c["name"] for c in checks}
    assert "provider_virtuals" in names
    assert "llm_model_not_banned" in names


def test_resolve_spark_runtime_dict_keys(monkeypatch):
    monkeypatch.setenv("VIRTUALS_API_KEY", "acp-" + "y" * 20)
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "spark")
    cfg = resolve_spark_runtime(bridge_keys=False)
    d = cfg.as_runtime_dict()
    assert d["provider"] == "virtuals"
    assert d["llm_model"] == DEFAULT_MODEL_STANDARD