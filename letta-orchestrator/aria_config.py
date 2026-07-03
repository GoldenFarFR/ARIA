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

# PC 8 Go VRAM — jamais de qwen 32b local (Sylvain 2026-07-02)
QWEN_LOCAL = os.environ.get("ARIA_OLLAMA_MODEL", "qwen2.5:14b")
QWEN_SCOUT = os.environ.get("ARIA_SCOUT_MODEL", QWEN_LOCAL)
QWEN_CLASSIFIER = os.environ.get("ARIA_CLASSIFIER_MODEL", QWEN_SCOUT)
EMBEDDING = os.environ.get("ARIA_EMBEDDING_MODEL", "ollama/nomic-embed-text:latest")
OLLAMA_LOCAL = f"ollama/{QWEN_LOCAL}"

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
    "plan en", "fusionner", "refactorise",
)

MOYEN_HINTS = (
    "explique", "diagnostique", "pourquoi", "compare", "comment faire",
    "décris", "analyse", "étapes", "problème",
)

SIMPLE_HINTS = (
    "bonjour", "salut", "merci", "coucou", "hello", "ping",
    "qui es-tu", "qui es tu", "comment vas", "quel est mon", "je m'appelle",
    "dis bonjour", "en une phrase",
    "presente toi", "présente toi", "présente-toi", "presente-toi",
    "ton identité", "ton identite", "ta fonction", "ta identité", "ta identite",
)

OLLAMA_NUM_CTX = int(os.environ.get("ARIA_OLLAMA_NUM_CTX", "8192"))


def _read_vault_env() -> dict[str, str]:
    vault = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
    out: dict[str, str] = {}
    for name in ("local.env", "production.env"):
        path = vault / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"')
            if key and val and key not in out:
                out[key] = val
    return out


def bridge_api_keys() -> dict[str, bool]:
    """Aligne les noms de clés Letta avec le coffre GoldenFar / profil PowerShell."""
    status: dict[str, bool] = {}
    vault = _read_vault_env()

    if not os.environ.get("XAI_API_KEY"):
        for src in ("XAI_API_KEY", "GROK_API_KEY", "IMAGE_API_KEY"):
            val = os.environ.get(src) or vault.get(src)
            if val:
                os.environ["XAI_API_KEY"] = val
                break

    groq = os.environ.get("GROQ_API_KEY") or ""
    if len(groq) < 20:
        for src in ("LLM_API_KEY", "GROQ_API_KEY"):
            candidate = vault.get(src) or os.environ.get(src) or ""
            if len(candidate) >= 20:
                os.environ["GROQ_API_KEY"] = candidate
                break
    elif not os.environ.get("GROQ_API_KEY") and os.environ.get("LLM_API_KEY"):
        os.environ["GROQ_API_KEY"] = os.environ["LLM_API_KEY"]

    for key in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OLLAMA_BASE_URL"):
        status[key] = bool(os.environ.get(key))
    return status


def _groq_key_ok() -> bool:
    import requests

    key = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY") or ""
    if len(key) < 20:
        return False
    # Clé Groq valide en forme → pas d'appel réseau (évite timeout au démarrage)
    if key.startswith("gsk_") and len(key) >= 40:
        return True
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return key.startswith("gsk_")


def resolve_models() -> dict[str, str]:
    """Choisit les modèles selon les clés disponibles (Letta 0.6.7 local)."""
    bridge_api_keys()
    groq_ok = _groq_key_ok()
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))

    scout = f"ollama/{QWEN_SCOUT}"
    # Groq/Anthropic cloud si dispo ; sinon toujours qwen2.5:14b local (pas de 32b)
    grok = "groq/llama-3.3-70b-versatile" if groq_ok else OLLAMA_LOCAL

    if has_anthropic:
        core = "anthropic/claude-3-5-sonnet-20241022"
    elif groq_ok:
        core = "groq/llama-3.3-70b-versatile"
    else:
        core = OLLAMA_LOCAL

    return {"scout": scout, "grok": grok, "core": core, "embedding": EMBEDDING}