"""ARIA shell — cerveau aria-core (vector + COLLEGUE) sans Telegram ni HTTP."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    raw = os.environ.get("ARIA_REPO_ROOT", "").strip()
    if raw and Path(raw).is_dir():
        return Path(raw)
    default = Path.home() / "GitHub-Repos" / "ARIA"
    return default if default.is_dir() else Path.cwd()


_PROD_OVERLAY = frozenset({
    "LLM_PROVIDER", "LLM_MODEL", "VIRTUALS_API_KEY", "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL", "ARIA_SPARK_AGGRESSIVE",
    "ARIA_LLM_MODEL_DEVELOP", "ARIA_LLM_MODEL_STANDARD", "ARIA_LLM_MODEL_BRIEF",
    "ARIA_OUVRIER_CLOUD", "ARIA_OUVRIER_SKIP_GROQ_FALLBACK",
})


def _read_vault_env() -> dict[str, str]:
    vault = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
    out: dict[str, str] = {}
    for name in ("local.env", "production.env"):
        path = vault / name
        if not path.is_file():
            continue
        parsed: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"')
            if key and val:
                parsed[key] = val
        if name == "local.env":
            for key, val in parsed.items():
                if key not in _PROD_OVERLAY:
                    out[key] = val
        else:
            out.update(parsed)
    return out


def _spark_settings() -> dict[str, str | bool]:
    vault = _read_vault_env()
    virtuals_key = os.environ.get("VIRTUALS_API_KEY") or vault.get("VIRTUALS_API_KEY") or ""
    ouvrier_cloud = (
        os.environ.get("ARIA_OUVRIER_CLOUD") or vault.get("ARIA_OUVRIER_CLOUD") or ""
    ).strip().lower()
    provider = (os.environ.get("LLM_PROVIDER") or vault.get("LLM_PROVIDER") or "").strip().lower()
    if (
        ouvrier_cloud in ("spark", "virtuals")
        or (vault.get("LLM_PROVIDER") or "").strip().lower() == "virtuals"
        or len(virtuals_key) >= 10
    ):
        provider = "virtuals"
    elif not provider:
        provider = "ollama"
    if virtuals_key:
        os.environ["VIRTUALS_API_KEY"] = virtuals_key
    groq_fb = (
        os.environ.get("LLM_FALLBACK_API_KEY")
        or vault.get("LLM_FALLBACK_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    )
    return {
        "provider": provider,
        "llm_model": os.environ.get("LLM_MODEL") or vault.get("LLM_MODEL") or "deepseek-deepseek-v4-pro",
        "virtuals_api_key": virtuals_key,
        "llm_fallback_api_key": groq_fb,
        "llm_fallback_provider": vault.get("LLM_FALLBACK_PROVIDER") or "groq",
        "llm_fallback_model": vault.get("LLM_FALLBACK_MODEL") or "llama-3.3-70b-versatile",
        "aria_spark_aggressive": (vault.get("ARIA_SPARK_AGGRESSIVE") or "").lower() in ("1", "true", "yes"),
        "aria_llm_model_develop": vault.get("ARIA_LLM_MODEL_DEVELOP") or "anthropic-claude-opus-4-8",
        "aria_llm_model_standard": vault.get("ARIA_LLM_MODEL_STANDARD") or "x-ai-grok-4-3",
        "aria_llm_model_brief": vault.get("ARIA_LLM_MODEL_BRIEF") or "deepseek-deepseek-v4-flash",
    }


def _bootstrap() -> Path:
    repo = _repo_root()
    os.environ.setdefault("ARIA_REPO_ROOT", str(repo))
    data = os.environ.get("DATA_DIR", "").strip()
    if not data:
        data = str(repo / "vanguard" / "backend" / "data")
        os.environ["DATA_DIR"] = data
    data_dir = Path(data)
    data_dir.mkdir(parents=True, exist_ok=True)

    for key, value in (
        ("ARIA_VECTOR_MEMORY", "true"),
        ("ARIA_MEMORY_ARBITRATOR", "true"),
        ("ARIA_DDG_SEARCH_CACHE", "true"),
        ("ARIA_PUBLIC_MODE", "false"),
        ("ARIA_LLM_ENABLED", "true"),
        ("ACCESS_CODE_ENABLED", "false"),
    ):
        os.environ.setdefault(key, value)
    spark = _spark_settings()
    os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    def _flag(name: str, default: bool = False) -> bool:
        return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")

    founder = _flag("ARIA_OPERATOR_FOUNDER_MODE")
    depth_default = (os.environ.get("ARIA_LLM_DEPTH_DEFAULT") or "").strip()
    if not depth_default and founder:
        depth_default = "standard"

    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=_flag("ARIA_VECTOR_MEMORY"),
            aria_ddg_search_cache=_flag("ARIA_DDG_SEARCH_CACHE"),
            aria_memory_arbitrator=_flag("ARIA_MEMORY_ARBITRATOR", True),
            aria_public_mode=False,
            aria_llm_enabled=_flag("ARIA_LLM_ENABLED", True),
            llm_provider=str(spark["provider"]),
            llm_model=str(spark["llm_model"]),
            virtuals_api_key=str(spark["virtuals_api_key"]),
            llm_api_key=str(spark["llm_fallback_api_key"]),
            llm_fallback_api_key=str(spark["llm_fallback_api_key"]),
            llm_fallback_provider=str(spark["llm_fallback_provider"]),
            llm_fallback_model=str(spark["llm_fallback_model"]),
            aria_spark_aggressive=bool(spark["aria_spark_aggressive"]),
            aria_operator_founder_mode=founder,
            aria_llm_depth_default=depth_default or "brief",
            aria_epistemic_web_verify=_flag("ARIA_EPISTEMIC_WEB_VERIFY", not founder),
            aria_epistemic_critic=_flag("ARIA_EPISTEMIC_CRITIC", not founder),
            aria_llm_cost_footer=_flag("ARIA_LLM_COST_FOOTER", not founder),
            aria_llm_max_tokens_standard=int(os.environ.get("ARIA_LLM_MAX_TOKENS_STANDARD", "400")),
            aria_llm_max_tokens_develop=int(os.environ.get("ARIA_LLM_MAX_TOKENS_DEVELOP", "900")),
            aria_llm_model_develop=str(spark["aria_llm_model_develop"]),
            aria_llm_model_standard=str(spark["aria_llm_model_standard"]),
            aria_llm_model_brief=str(spark["aria_llm_model_brief"]),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            aria_ollama_num_ctx=int(os.environ.get("ARIA_OLLAMA_NUM_CTX", "8192")),
            aria_autonomous=_flag("ARIA_AUTONOMOUS", True),
        ),
    )
    return data_dir


async def _run(message: str, *, json_out: bool) -> int:
    _bootstrap()
    from aria_core import repertoire_db
    from aria_core.brain import aria_brain
    from aria_core.locale import LANG_FR

    await repertoire_db.init_repertoire_db()

    result = await aria_brain.process(
        message.strip(),
        lang=LANG_FR,
        visitor_id="shell-operateur",
        public_mode=False,
    )
    if json_out:
        print(
            json.dumps(
                {
                    "reply": result.reply,
                    "skill": str(result.skill_used) if result.skill_used else None,
                    "actions": result.actions_taken,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(result.reply)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARIA shell brain")
    parser.add_argument("--message", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.message.strip():
        print("[Erreur] message vide", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(args.message, json_out=args.json))
    except Exception as exc:
        print(f"[Erreur] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())