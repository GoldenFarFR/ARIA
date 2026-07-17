"""SSOT Spark/Virtuals (aria_core.spark_config) -- aucune couverture jusqu'ici.
Toutes les fonctions testées ici acceptent un ``vault`` explicite -- on ne touche
jamais au vrai vault filesystem (``read_merged_vault_env``/``vault_root``), et on
isole systématiquement les variables d'environnement pertinentes."""
from __future__ import annotations

import pytest

from aria_core import spark_config as sc

_ENV_KEYS = (
    "LLM_MODEL", "ARIA_LLM_MODEL_STANDARD", "ARIA_LLM_MODEL_DEVELOP", "ARIA_LLM_MODEL_BRIEF",
    "ARIA_LLM_DEPTH_DEFAULT", "ARIA_SPARK_AGGRESSIVE", "ARIA_OUVRIER_CLOUD", "LLM_PROVIDER",
    "VIRTUALS_API_KEY", "LLM_FALLBACK_API_KEY", "GROQ_API_KEY", "LLM_API_KEY",
    "LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_normalize_model_id_strips_x_ai_prefix():
    assert sc.normalize_model_id("x-ai-grok-4-3") == "grok-4-3"
    assert sc.normalize_model_id("x_ai_grok-4-3") == "grok-4-3"
    assert sc.normalize_model_id("GROK-4-3") == "grok-4-3"


def test_models_equivalent_across_prefix_and_case():
    assert sc.models_equivalent("x-ai-grok-4-3", "grok-4-3")
    assert sc.models_equivalent("X-AI-GROK-4-3", "grok-4-3")
    assert not sc.models_equivalent("grok-4-3", "opus-4-8")


def test_vault_get_prefers_environ_over_vault(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert sc.vault_get("LLM_MODEL", vault={"LLM_MODEL": "from-vault"}) == "from-env"


def test_vault_get_falls_back_to_vault_when_env_absent():
    assert sc.vault_get("LLM_MODEL", vault={"LLM_MODEL": "from-vault"}) == "from-vault"


def test_vault_get_tries_multiple_names_in_order():
    assert sc.vault_get("MISSING_KEY", "LLM_MODEL", vault={"LLM_MODEL": "fallback-name"}) == "fallback-name"


def test_vault_get_empty_when_nothing_found():
    assert sc.vault_get("NOTHING_HERE", vault={}) == ""


def test_resolve_primary_llm_model_uses_env_first(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    assert sc.resolve_primary_llm_model(vault={}) == "custom-model"


def test_resolve_primary_llm_model_skips_banned_model(monkeypatch):
    # 17/07 -- BANNED_VIRTUALS_PRIMARY_MODELS est vide par défaut désormais (décision
    # opérateur explicite : plus aucun modèle réel n'a de raison documentée d'être banni)
    # -- le mécanisme lui-même reste testé via une valeur synthétique.
    monkeypatch.setattr(sc, "BANNED_VIRTUALS_PRIMARY_MODELS", frozenset({"synthetic-banned-model"}))
    monkeypatch.setenv("LLM_MODEL", "synthetic-banned-model")
    result = sc.resolve_primary_llm_model(vault={"ARIA_LLM_MODEL_STANDARD": "safe-model"})
    assert result == "safe-model"
    assert result not in sc.BANNED_VIRTUALS_PRIMARY_MODELS


def test_resolve_primary_llm_model_defaults_when_nothing_set():
    assert sc.resolve_primary_llm_model(vault={}) == sc.DEFAULT_MODEL_STANDARD


def test_spark_model_for_prompt_explicit_depth_brief():
    vault = {"ARIA_LLM_MODEL_BRIEF": "brief-model"}
    assert sc.spark_model_for_prompt("/depth brief explique-moi ça", vault=vault) == "brief-model"


def test_spark_model_for_prompt_explicit_depth_develop():
    vault = {"ARIA_LLM_MODEL_DEVELOP": "develop-model"}
    assert sc.spark_model_for_prompt("/depth develop analyse complète", vault=vault) == "develop-model"


def test_spark_model_for_prompt_develop_hint_keyword():
    vault = {"ARIA_LLM_MODEL_DEVELOP": "develop-model"}
    assert sc.spark_model_for_prompt("développe ton architecture stp", vault=vault) == "develop-model"


def test_spark_model_for_prompt_long_text_triggers_develop():
    vault = {"ARIA_LLM_MODEL_DEVELOP": "develop-model"}
    long_text = "x" * 500
    assert sc.spark_model_for_prompt(long_text, vault=vault) == "develop-model"


def test_spark_model_for_prompt_short_generic_uses_standard():
    vault = {"ARIA_LLM_MODEL_STANDARD": "standard-model", "LLM_MODEL": "standard-model"}
    assert sc.spark_model_for_prompt("salut ça va ?", vault=vault) == "standard-model"


def test_spark_model_for_prompt_aggressive_default_develop_forces_develop():
    vault = {
        "ARIA_SPARK_AGGRESSIVE": "true",
        "ARIA_LLM_DEPTH_DEFAULT": "develop",
        "ARIA_LLM_MODEL_DEVELOP": "develop-model",
    }
    assert sc.spark_model_for_prompt("salut", vault=vault) == "develop-model"


def test_use_spark_chain_true_when_ouvrier_cloud_spark(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "spark")
    assert sc.use_spark_chain(vault={}) is True


def test_use_spark_chain_true_when_provider_virtuals():
    assert sc.use_spark_chain(vault={"LLM_PROVIDER": "virtuals"}) is True


def test_use_spark_chain_false_without_any_signal():
    assert sc.use_spark_chain(vault={}) is False


def test_resolve_provider_returns_spark_when_virtuals_key_present(monkeypatch):
    monkeypatch.setenv("VIRTUALS_API_KEY", "a-very-long-virtuals-key-1234567890")
    assert sc.resolve_provider(vault={}) == sc.DEFAULT_PROVIDER_SPARK


def test_resolve_provider_returns_explicit_provider_without_virtuals(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    assert sc.resolve_provider(vault={}) == "groq"


def test_resolve_provider_defaults_to_grok_when_nothing_configured():
    assert sc.resolve_provider(vault={}) == "grok"


def test_resolve_provider_env_llm_provider_overrides_stale_vault_default(monkeypatch):
    """17/07, bug réel : sur le VPS (aucun local.env/production.env, concept Windows),
    le vault mergé == les défauts du registre ({"LLM_PROVIDER": "virtuals", ...}) --
    un `.env` posant LLM_PROVIDER=grok doit gagner, jamais rester sans effet. Reproduit
    exactement le scénario prod (VIRTUALS_API_KEY absente, ARIA_OUVRIER_CLOUD=grok en
    env, mais le vault -- simulant le registre -- dit encore "virtuals"/"spark")."""
    monkeypatch.delenv("VIRTUALS_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "grok")
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "grok")
    stale_vault = {"LLM_PROVIDER": "virtuals", "ARIA_OUVRIER_CLOUD": "spark"}
    assert sc.resolve_provider(vault=stale_vault) == "grok"


def test_apply_spark_to_environ_sets_defaults_without_overwriting(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "already-set")
    cfg = sc.SparkRuntimeConfig(
        provider="virtuals", llm_model="grok-4-3", virtuals_api_key="key123456789",
        llm_fallback_api_key="", llm_fallback_provider="groq", llm_fallback_model="llama-3",
        aria_spark_aggressive=False, aria_llm_model_develop="opus", aria_llm_model_standard="grok",
        aria_llm_model_brief="deepseek", aria_ouvrier_cloud="spark",
    )
    sc.apply_spark_to_environ(cfg)
    import os

    assert os.environ["LLM_PROVIDER"] == "already-set"  # setdefault -- jamais écrasé
    assert os.environ["VIRTUALS_API_KEY"] == "key123456789"
    assert os.environ["ARIA_LLM_MODEL_STANDARD"] == "grok"


def test_spark_runtime_config_as_runtime_dict_round_trips_all_fields():
    cfg = sc.SparkRuntimeConfig(
        provider="virtuals", llm_model="grok-4-3", virtuals_api_key="key",
        llm_fallback_api_key="fb-key", llm_fallback_provider="groq", llm_fallback_model="llama-3",
        aria_spark_aggressive=True, aria_llm_model_develop="opus", aria_llm_model_standard="grok",
        aria_llm_model_brief="deepseek", aria_ouvrier_cloud="spark",
    )
    d = cfg.as_runtime_dict()
    assert d["provider"] == "virtuals"
    assert d["aria_spark_aggressive"] is True
    assert d["llm_fallback_model"] == "llama-3"


def test_verify_spark_alignment_all_checks_pass_when_configured(monkeypatch):
    monkeypatch.setenv("VIRTUALS_API_KEY", "a-very-long-virtuals-key-1234567890")
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "spark")
    monkeypatch.setenv("LLM_PROVIDER", "virtuals")
    monkeypatch.setenv("LLM_MODEL", sc.DEFAULT_MODEL_STANDARD)

    checks = sc.verify_spark_alignment()
    names = {c["name"]: c["ok"] for c in checks}
    assert names["provider_virtuals"] is True
    assert names["virtuals_key"] is True
    assert names["llm_model_not_banned"] is True
    assert names["ouvrier_cloud_spark"] is True


def test_verify_spark_alignment_returns_named_boolean_checks():
    """verify_spark_alignment() lit toujours le VRAI vault (pas d'injection possible) --
    on ne peut donc pas forcer un état "non configuré" de façon fiable ici (dépend du
    vault réel de l'hôte). On vérifie la FORME du résultat, pas une valeur précise."""
    checks = sc.verify_spark_alignment()
    names = {c["name"] for c in checks}
    assert "provider_virtuals" in names
    assert "virtuals_key" in names
    assert all(isinstance(c["ok"], bool) for c in checks)
    assert all(isinstance(c["detail"], str) for c in checks)
