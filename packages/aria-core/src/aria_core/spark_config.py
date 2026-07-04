"""SSOT Spark / Virtuals — coffre → cerveau → ouvrier → shell (un seul alignement)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aria_core.ecosystem_config import (  # noqa: E402
    prod_overlay_keys as _prod_overlay_keys,
    propagate_operator_env,
    read_merged_vault_env as _read_merged_vault_env,
    registry_defaults,
    vault_root as _vault_root,
)

# SSOT ecosystem_registry.yaml
PROD_OVERLAY_KEYS = _prod_overlay_keys()

_defaults = registry_defaults()
DEFAULT_PROVIDER_SPARK = _defaults.get("LLM_PROVIDER", "virtuals")
DEFAULT_MODEL_STANDARD = _defaults.get("ARIA_LLM_MODEL_STANDARD", "x-ai-grok-4-3")
DEFAULT_MODEL_DEVELOP = _defaults.get("ARIA_LLM_MODEL_DEVELOP", "anthropic-claude-opus-4-8")
DEFAULT_MODEL_BRIEF = _defaults.get("ARIA_LLM_MODEL_BRIEF", "deepseek-deepseek-v4-flash")
DEFAULT_FALLBACK_PROVIDER = _defaults.get("LLM_FALLBACK_PROVIDER", "groq")
DEFAULT_FALLBACK_MODEL = _defaults.get("LLM_FALLBACK_MODEL", "llama-3.3-70b-versatile")
VIRTUALS_CHAT_URL = "https://compute.virtuals.io/v1/chat/completions"

from aria_core.ecosystem_config import banned_values as _banned_values

# Virtuals renvoie vide sur ces modèles en primaire (ecosystem_registry.yaml)
BANNED_VIRTUALS_PRIMARY_MODELS = _banned_values().get(
    "LLM_MODEL", frozenset({"deepseek-deepseek-v4-pro"})
)

_DEVELOP_HINT = re.compile(
    r"(?i)\b(?:développe|develop|architecture|roadmap|analyse\s+complète|full\s+analysis|"
    r"deep\s+dive|en\s+profondeur)\b"
)


def vault_root() -> Path:
    return _vault_root()


def read_merged_vault_env() -> dict[str, str]:
    """SSOT ecosystem_config — coffre mergé + propagation."""
    return propagate_operator_env(_read_merged_vault_env())


def vault_get(*names: str, vault: dict[str, str] | None = None) -> str:
    merged = vault if vault is not None else read_merged_vault_env()
    for name in names:
        val = (os.environ.get(name) or merged.get(name) or "").strip()
        if val:
            return val
    return ""


def virtuals_api_key(vault: dict[str, str] | None = None) -> str:
    key = vault_get("VIRTUALS_API_KEY", vault=vault)
    if len(key) >= 10:
        return key
    vk = vault_root() / "keys" / "virtuals.api-key"
    if vk.is_file():
        return vk.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def _flag_true(name: str, vault: dict[str, str]) -> bool:
    raw = (os.environ.get(name) or vault.get(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def resolve_primary_llm_model(vault: dict[str, str] | None = None) -> str:
    """LLM_MODEL primaire — jamais un modèle banni sur Virtuals."""
    merged = vault if vault is not None else read_merged_vault_env()
    for candidate in (
        os.environ.get("LLM_MODEL"),
        merged.get("LLM_MODEL"),
        merged.get("ARIA_LLM_MODEL_STANDARD"),
        os.environ.get("ARIA_LLM_MODEL_STANDARD"),
        DEFAULT_MODEL_STANDARD,
    ):
        model = (candidate or "").strip()
        if model and model not in BANNED_VIRTUALS_PRIMARY_MODELS:
            return model
    return DEFAULT_MODEL_STANDARD


def spark_model_for_prompt(prompt: str, vault: dict[str, str] | None = None) -> str:
    """Routage depth ouvrier + cerveau calibré — SSOT."""
    text = (prompt or "").strip()
    merged = vault if vault is not None else read_merged_vault_env()
    if re.search(r"(?i)/depth\s+develop\b", text) or _DEVELOP_HINT.search(text) or len(text) > 420:
        return vault_get("ARIA_LLM_MODEL_DEVELOP", vault=merged) or DEFAULT_MODEL_DEVELOP
    if re.search(r"(?i)/depth\s+brief\b", text):
        return vault_get("ARIA_LLM_MODEL_BRIEF", vault=merged) or DEFAULT_MODEL_BRIEF
    standard = vault_get("ARIA_LLM_MODEL_STANDARD", vault=merged) or DEFAULT_MODEL_STANDARD
    primary = resolve_primary_llm_model(merged)
    if primary not in BANNED_VIRTUALS_PRIMARY_MODELS:
        return standard if standard else primary
    return DEFAULT_MODEL_STANDARD


def use_spark_chain(vault: dict[str, str] | None = None) -> bool:
    merged = vault if vault is not None else read_merged_vault_env()
    env_cloud = (os.environ.get("ARIA_OUVRIER_CLOUD") or "").strip().lower()
    vault_cloud = (merged.get("ARIA_OUVRIER_CLOUD") or "").strip().lower()
    if env_cloud in ("spark", "virtuals") or vault_cloud in ("spark", "virtuals"):
        return True
    if (merged.get("LLM_PROVIDER") or "").strip().lower() == "virtuals":
        return True
    return len(virtuals_api_key(merged)) >= 10


def resolve_provider(vault: dict[str, str] | None = None) -> str:
    merged = vault if vault is not None else read_merged_vault_env()
    vk = virtuals_api_key(merged)
    ouvrier_cloud = (
        os.environ.get("ARIA_OUVRIER_CLOUD") or merged.get("ARIA_OUVRIER_CLOUD") or ""
    ).strip().lower()
    vault_provider = (merged.get("LLM_PROVIDER") or "").strip().lower()
    provider = (os.environ.get("LLM_PROVIDER") or vault_provider or "").strip().lower()
    if (
        ouvrier_cloud in ("spark", "virtuals")
        or vault_provider == "virtuals"
        or len(vk) >= 10
    ):
        return DEFAULT_PROVIDER_SPARK
    if provider:
        return provider
    return "grok"


@dataclass(frozen=True)
class SparkRuntimeConfig:
    provider: str
    llm_model: str
    virtuals_api_key: str
    llm_fallback_api_key: str
    llm_fallback_provider: str
    llm_fallback_model: str
    aria_spark_aggressive: bool
    aria_llm_model_develop: str
    aria_llm_model_standard: str
    aria_llm_model_brief: str
    aria_ouvrier_cloud: str

    def as_runtime_dict(self) -> dict[str, str | bool]:
        return {
            "provider": self.provider,
            "llm_model": self.llm_model,
            "virtuals_api_key": self.virtuals_api_key,
            "llm_fallback_api_key": self.llm_fallback_api_key,
            "llm_fallback_provider": self.llm_fallback_provider,
            "llm_fallback_model": self.llm_fallback_model,
            "aria_spark_aggressive": self.aria_spark_aggressive,
            "aria_llm_model_develop": self.aria_llm_model_develop,
            "aria_llm_model_standard": self.aria_llm_model_standard,
            "aria_llm_model_brief": self.aria_llm_model_brief,
            "aria_ouvrier_cloud": self.aria_ouvrier_cloud,
        }


def bridge_spark_keys(vault: dict[str, str] | None = None) -> None:
    """Injecte VIRTUALS_API_KEY dans os.environ depuis coffre."""
    vk = virtuals_api_key(vault)
    if vk and len(os.environ.get("VIRTUALS_API_KEY", "")) < 10:
        os.environ["VIRTUALS_API_KEY"] = vk


def resolve_spark_runtime(*, bridge_keys: bool = True) -> SparkRuntimeConfig:
    """Point d'entrée unique — cerveau, shell_chat, orchestrate_unified."""
    vault = read_merged_vault_env()
    if bridge_keys:
        bridge_spark_keys(vault)
        try:
            from aria_config import bridge_api_keys  # type: ignore[import-not-found]

            bridge_api_keys()
        except ImportError:
            pass
    vk = virtuals_api_key(vault)
    if vk and len(os.environ.get("VIRTUALS_API_KEY", "")) < 10:
        os.environ["VIRTUALS_API_KEY"] = vk

    groq_fb = (
        os.environ.get("LLM_FALLBACK_API_KEY")
        or vault.get("LLM_FALLBACK_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    )
    ouvrier_cloud = (
        os.environ.get("ARIA_OUVRIER_CLOUD") or vault.get("ARIA_OUVRIER_CLOUD") or "spark"
    ).strip().lower()

    return SparkRuntimeConfig(
        provider=resolve_provider(vault),
        llm_model=resolve_primary_llm_model(vault),
        virtuals_api_key=vk,
        llm_fallback_api_key=groq_fb,
        llm_fallback_provider=(
            os.environ.get("LLM_FALLBACK_PROVIDER")
            or vault.get("LLM_FALLBACK_PROVIDER")
            or DEFAULT_FALLBACK_PROVIDER
        ),
        llm_fallback_model=(
            os.environ.get("LLM_FALLBACK_MODEL")
            or vault.get("LLM_FALLBACK_MODEL")
            or DEFAULT_FALLBACK_MODEL
        ),
        aria_spark_aggressive=_flag_true("ARIA_SPARK_AGGRESSIVE", vault),
        aria_llm_model_develop=vault_get("ARIA_LLM_MODEL_DEVELOP", vault=vault) or DEFAULT_MODEL_DEVELOP,
        aria_llm_model_standard=vault_get("ARIA_LLM_MODEL_STANDARD", vault=vault) or DEFAULT_MODEL_STANDARD,
        aria_llm_model_brief=vault_get("ARIA_LLM_MODEL_BRIEF", vault=vault) or DEFAULT_MODEL_BRIEF,
        aria_ouvrier_cloud=ouvrier_cloud or "spark",
    )


def apply_spark_to_environ(cfg: SparkRuntimeConfig) -> None:
    """Aligne os.environ pour toute la session KART."""
    os.environ.setdefault("ARIA_LLM_ENABLED", "true")
    os.environ.setdefault("LLM_PROVIDER", cfg.provider)
    os.environ.setdefault("LLM_MODEL", cfg.llm_model)
    if cfg.virtuals_api_key:
        os.environ.setdefault("VIRTUALS_API_KEY", cfg.virtuals_api_key)
    os.environ.setdefault("ARIA_OUVRIER_CLOUD", cfg.aria_ouvrier_cloud)
    os.environ.setdefault("ARIA_LLM_MODEL_STANDARD", cfg.aria_llm_model_standard)
    os.environ.setdefault("ARIA_LLM_MODEL_DEVELOP", cfg.aria_llm_model_develop)
    os.environ.setdefault("ARIA_LLM_MODEL_BRIEF", cfg.aria_llm_model_brief)


def verify_spark_alignment() -> list[dict[str, Any]]:
    """Checks PASS/FAIL — verify-spark-routing.ps1 + CI."""
    vault = read_merged_vault_env()
    cfg = resolve_spark_runtime(bridge_keys=False)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("provider_virtuals", cfg.provider == "virtuals", cfg.provider)
    add("virtuals_key", len(cfg.virtuals_api_key) >= 10, f"len={len(cfg.virtuals_api_key)}")
    add(
        "llm_model_not_banned",
        cfg.llm_model not in BANNED_VIRTUALS_PRIMARY_MODELS,
        cfg.llm_model,
    )
    add(
        "llm_model_matches_standard_or_grok",
        cfg.llm_model == cfg.aria_llm_model_standard or "grok" in cfg.llm_model.lower(),
        f"primary={cfg.llm_model} standard={cfg.aria_llm_model_standard}",
    )
    add("ouvrier_cloud_spark", cfg.aria_ouvrier_cloud in ("spark", "virtuals"), cfg.aria_ouvrier_cloud)
    add("spark_chain", use_spark_chain(vault), "use_spark_chain")
    develop = spark_model_for_prompt("/depth develop test", vault)
    standard = spark_model_for_prompt("salut", vault)
    add("develop_model_opus", "opus" in develop.lower(), develop)
    add("standard_model_grok", "grok" in standard.lower(), standard)

    vault_lm = (vault.get("LLM_MODEL") or "").strip()
    if vault_lm in BANNED_VIRTUALS_PRIMARY_MODELS:
        add(
            "vault_llm_model_overridden",
            cfg.llm_model not in BANNED_VIRTUALS_PRIMARY_MODELS,
            f"vault LLM_MODEL={vault_lm} runtime={cfg.llm_model}",
        )

    return checks