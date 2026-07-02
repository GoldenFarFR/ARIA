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
    os.environ.setdefault("LLM_PROVIDER", "ollama")
    os.environ.setdefault("LLM_MODEL", "qwen2.5:14b")
    os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    def _flag(name: str, default: bool = False) -> bool:
        return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")

    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=_flag("ARIA_VECTOR_MEMORY"),
            aria_ddg_search_cache=_flag("ARIA_DDG_SEARCH_CACHE"),
            aria_memory_arbitrator=_flag("ARIA_MEMORY_ARBITRATOR", True),
            aria_public_mode=False,
            aria_llm_enabled=_flag("ARIA_LLM_ENABLED", True),
            llm_provider=os.environ.get("LLM_PROVIDER", "ollama"),
            llm_model=os.environ.get("LLM_MODEL", "qwen2.5:14b"),
            llm_api_key=os.environ.get("LLM_API_KEY", "") or os.environ.get("GROQ_API_KEY", ""),
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