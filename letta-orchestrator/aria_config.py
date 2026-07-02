"""Configuration partagée ARIA Letta — chemins et modèles alignés monorepo."""
from __future__ import annotations

import os
from pathlib import Path

# Racine monorepo (pas C:\ARIA)
ARIA_REPO_ROOT = Path(
    os.environ.get("ARIA_REPO_ROOT", Path.home() / "GitHub-Repos" / "ARIA")
).resolve()

LETTA_DIR = Path(__file__).resolve().parent
CONFIG_PATH = LETTA_DIR / "agents_config.json"
MODELS_PATH = LETTA_DIR / "models_config.json"

LETTA_URL = os.environ.get("LETTA_URL", "http://localhost:8283")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL}/api/chat"

QWEN_SCOUT = os.environ.get("ARIA_SCOUT_MODEL", "qwen2.5:14b")
QWEN_CLASSIFIER = os.environ.get("ARIA_CLASSIFIER_MODEL", QWEN_SCOUT)
EMBEDDING = os.environ.get("ARIA_EMBEDDING_MODEL", "ollama/nomic-embed-text:latest")

DEFAULT_MEMORY_BLOCKS = [
    {
        "label": "persona",
        "value": (
            "Tu es un agent ARIA du monorepo GoldenFar. "
            "Réponses structurées, professionnelles, en français."
        ),
        "description": "Personnalité ARIA",
    },
    {
        "label": "human",
        "value": "Opérateur : Sylvain Rio (GoldenFarFR). Projet : ARIA ZHC.",
        "description": "Contexte humain",
    },
]

COMPLEX_HINTS = (
    "architecture", "refactor", "plusieurs fichiers", "migration",
    "réécrire", "debug complexe", "performance", "sécurité", "redesign",
)


def bridge_api_keys() -> dict[str, bool]:
    """Aligne les noms de clés Letta avec le coffre GoldenFar / profil PowerShell."""
    status: dict[str, bool] = {}

    if not os.environ.get("XAI_API_KEY"):
        for src in ("XAI_API_KEY", "GROK_API_KEY", "IMAGE_API_KEY"):
            val = os.environ.get(src)
            if val:
                os.environ["XAI_API_KEY"] = val
                break

    if not os.environ.get("GROQ_API_KEY") and os.environ.get("LLM_API_KEY"):
        os.environ["GROQ_API_KEY"] = os.environ["LLM_API_KEY"]

    for key in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OLLAMA_BASE_URL"):
        status[key] = bool(os.environ.get(key))
    return status


def _groq_key_ok() -> bool:
    import requests

    key = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    if len(key) < 20:
        return False
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False


def resolve_models() -> dict[str, str]:
    """Choisit les modèles selon les clés disponibles (Letta 0.6.7 local)."""
    bridge_api_keys()
    groq_ok = _groq_key_ok()
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))

    scout = f"ollama/{QWEN_SCOUT}"
    # Letta 0.6.7 : pas de provider xai natif — Groq si clé valide, sinon Ollama 32b
    grok = "groq/llama-3.3-70b-versatile" if groq_ok else "ollama/qwen2.5:32b"

    if has_anthropic:
        core = "anthropic/claude-3-5-sonnet-20241022"
    elif groq_ok:
        core = "groq/llama-3.3-70b-versatile"
    else:
        core = "ollama/aria-qwen32b:latest"

    return {"scout": scout, "grok": grok, "core": core, "embedding": EMBEDDING}