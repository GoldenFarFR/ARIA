"""Ouvrier agentique direct (Grok/Groq/Ollama + outils) — sans Letta."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from pathlib import Path

from aria_config import ARIA_REPO_ROOT, bridge_api_keys
from ouvrier_tool_sources import TOOL_SOURCES
from ouvrier_proof import attach_proof, proof_after_tool
from ouvrier_trace import StepTimer, trace, trace_block

PERSONA_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_persona.md"
MAX_STEPS = 10

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_repo_file",
            "description": "Lire un fichier texte sous ARIA_REPO_ROOT.",
            "parameters": {
                "type": "object",
                "properties": {"rel_path": {"type": "string"}},
                "required": ["rel_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_repo_file",
            "description": "Écrire un fichier texte sous ARIA_REPO_ROOT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["rel_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "Exécuter PowerShell dans ARIA_REPO_ROOT (git, pytest, grep, etc.).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "session_handoff",
            "description": "Sync GitHub session-handoff (HANDOFF, COLLEGUE, JOURNAL).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_aria_worker",
            "description": "Lire la file ARIA-WORKER.md.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "triage_download_inbox",
            "description": "Trier la boîte download/.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_journal",
            "description": "Append une ligne au JOURNAL.md.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_vault_env",
            "description": "Modifier local.env ou production.env (coffre GoldenFar).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "target": {
                        "type": "string",
                        "description": "local, production, or both",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_local_quick",
            "description": "Lancer build-local.ps1 -Quick après modif code.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _load_tool_fns() -> dict[str, Any]:
    ns: dict[str, Any] = {"__builtins__": __builtins__}
    fns: dict[str, Any] = {}
    for spec in TOOL_SOURCES:
        exec(spec["source_code"], ns)  # noqa: S102
        fns[spec["name"]] = ns[spec["name"]]
    return fns


def _vault_key(*names: str) -> str:
    vault = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
    for fname in ("local.env", "production.env"):
        path = vault / fname
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for name in names:
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"')
    return ""


def _ouvrier_cloud_mode() -> str:
    """
    Politique moteur ouvrier KART.
    groq | grok | ollama | auto (défaut : grok si clé xAI, sinon groq).
    local = alias ollama.
    """
    raw = (
        os.environ.get("ARIA_OUVRIER_CLOUD", "").strip().lower()
        or _vault_key("ARIA_OUVRIER_CLOUD")
        or "auto"
    )
    if raw in ("local", "ollama"):
        return "ollama"
    if raw in ("groq", "grok", "auto"):
        return raw
    return "auto"


def _resolve_grok() -> tuple[str, str, str, str] | None:
    bridge_api_keys()
    xai = (
        os.environ.get("XAI_API_KEY")
        or os.environ.get("GROK_API_KEY")
        or os.environ.get("IMAGE_API_KEY")
        or _vault_key("XAI_API_KEY", "GROK_API_KEY", "IMAGE_API_KEY")
        or ""
    )
    if len(xai) >= 20:
        return "grok", "https://api.x.ai/v1/chat/completions", xai, "grok-3"
    return None


def _resolve_groq() -> tuple[str, str, str, str] | None:
    bridge_api_keys()
    groq = os.environ.get("GROQ_API_KEY") or _vault_key("GROQ_API_KEY", "LLM_API_KEY") or ""
    if len(groq) >= 40:
        return (
            "groq",
            "https://api.groq.com/openai/v1/chat/completions",
            groq,
            "llama-3.3-70b-versatile",
        )
    return None


def _resolve_ollama() -> tuple[str, str, None, str]:
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("ARIA_OLLAMA_MODEL", "qwen2.5:14b")
    return "ollama", f"{base}/v1/chat/completions", None, model


def _resolve_llm() -> tuple[str, str, str | None, str]:
    """Premier moteur affiché (provider_label / dash KART)."""
    mode = _ouvrier_cloud_mode()
    if mode == "ollama":
        return _resolve_ollama()
    if mode == "groq":
        groq = _resolve_groq()
        return groq if groq else _resolve_ollama()
    if mode == "grok":
        grok = _resolve_grok()
        if grok:
            return grok
        groq = _resolve_groq()
        return groq if groq else _resolve_ollama()
    grok = _resolve_grok()
    if grok:
        return grok
    groq = _resolve_groq()
    if groq:
        return groq
    return _resolve_ollama()


def _cloud_candidates() -> list[tuple[str, str, str | None, str]]:
    """Chaîne cloud selon ARIA_OUVRIER_CLOUD."""
    mode = _ouvrier_cloud_mode()
    if mode == "ollama":
        return []

    chain: list[tuple[str, str, str | None, str]] = []
    if mode == "groq":
        groq = _resolve_groq()
        if groq:
            chain.append(groq)
        return chain

    if mode == "grok":
        grok = _resolve_grok()
        if grok:
            chain.append(grok)
        groq = _resolve_groq()
        if groq and (not chain or groq[0] != chain[-1][0]):
            chain.append(groq)
        return chain

    grok = _resolve_grok()
    if grok:
        chain.append(grok)
    groq = _resolve_groq()
    if groq and (not chain or groq[0] != chain[-1][0]):
        chain.append(groq)
    return chain


def _chat(
    url: str,
    api_key: str | None,
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        body["tools"] = TOOL_SCHEMAS
        body["tool_choice"] = "auto"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    r = requests.post(url, headers=headers, json=body, timeout=300)
    if r.status_code in (403, 429):
        raise RuntimeError(f"LLM cloud indisponible ({r.status_code}).")
    r.raise_for_status()
    return r.json()


def _run_tool(fns: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    fn = fns.get(name)
    if not fn:
        return f"ERROR: unknown tool {name}"
    args_preview = json.dumps(args, ensure_ascii=False)[:200]
    with StepTimer(f"outil {name}({args_preview})"):
        try:
            result = fn(**args)
            out = str(result) if result is not None else "OK"
            proof = proof_after_tool(name, args, out)
            if proof:
                out = attach_proof(out, proof)
            trace_block("resultat", name, out, max_lines=8)
            return out
        except Exception as exc:
            trace("resultat", f"{name} ERROR: {exc}")
            return f"ERROR: {exc}"


def _ollama_chat(url: str, model: str, messages: list[dict[str, str]]) -> str:
    base = url.replace("/v1/chat/completions", "")
    r = requests.post(
        f"{base}/api/chat",
        json={"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.2}},
        timeout=300,
    )
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content") or ""


def run_ouvrier_ollama_react(user_prompt: str) -> str:
    """Fallback local : qwen + format ACTION/ARGS (sans tool API cloud)."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    url = f"{base}/v1/chat/completions"
    model = os.environ.get("ARIA_OLLAMA_MODEL", "qwen2.5:14b")
    persona = PERSONA_PATH.read_text(encoding="utf-8") if PERSONA_PATH.is_file() else ""
    tool_list = ", ".join(s["name"] for s in TOOL_SOURCES)
    system = (
        f"{persona}\n\n"
        f"Outils disponibles : {tool_list}.\n"
        "Pour AGIR, réponds avec exactement :\n"
        "ACTION: nom_outil\n"
        'ARGS: {"cle": "valeur"}\n\n'
        "Quand c'est fini :\n"
        "FINAL: réponse courte à Sylvain en français avec le RÉSULTAT (pas un plan).\n"
        "Si la demande implique du code ou un fichier : au moins un ACTION read/write avant FINAL.\n"
        "Ne demande pas à Sylvain de lancer des commandes."
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    fns = _load_tool_fns()

    trace("moteur", f"Ollama ReAct — {model}")
    for step in range(1, MAX_STEPS + 1):
        with StepTimer(f"ollama étape {step}/{MAX_STEPS}"):
            raw = _ollama_chat(url, model, messages).strip()
        trace_block("pensee", f"réponse modèle (étape {step})", raw, max_lines=10)
        final_m = re.search(r"(?is)^FINAL:\s*(.+)$", raw, re.MULTILINE)
        if final_m:
            return final_m.group(1).strip()

        act_m = re.search(r"(?im)^ACTION:\s*(\w+)", raw)
        args_m = re.search(r"(?is)^ARGS:\s*(\{.*\})", raw)
        if not act_m:
            return raw or "(pas de réponse)"

        name = act_m.group(1)
        args: dict[str, Any] = {}
        if args_m:
            try:
                args = json.loads(args_m.group(1))
            except json.JSONDecodeError:
                args = {}
        result = _run_tool(fns, name, args)
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"Résultat outil :\n{result[:6000]}\n\nContinue (ACTION ou FINAL)."})

    return "Limite d'étapes — demande plus courte ou réessaie après reset quota Groq/Grok."


def run_ouvrier(user_prompt: str) -> str:
    """Boucle agentique : grok → groq → Ollama ReAct."""
    candidates = _cloud_candidates()
    if not candidates:
        provider, _, _, model = _resolve_ollama()
        trace("moteur", f"Sélection → {provider}/{model}")
        return run_ouvrier_ollama_react(user_prompt)

    last_exc: Exception | None = None
    for index, (provider, url, api_key, model) in enumerate(candidates):
        if index == 0:
            trace("moteur", f"Sélection → {provider}/{model}")
        try:
            return _run_ouvrier_cloud(user_prompt, provider, url, api_key, model)
        except Exception as exc:
            last_exc = exc
            if index + 1 < len(candidates):
                nxt = candidates[index + 1]
                trace("fallback", f"{provider} KO ({exc}) → {nxt[0]}/{nxt[3]}")
                continue
            trace("fallback", f"{provider} KO ({exc}) → Ollama ReAct")
            return run_ouvrier_ollama_react(user_prompt)

    if last_exc:
        trace("fallback", f"Cloud KO ({last_exc}) → Ollama ReAct")
    return run_ouvrier_ollama_react(user_prompt)


def _run_ouvrier_cloud(
    user_prompt: str, provider: str, url: str, api_key: str | None, model: str
) -> str:
    persona = PERSONA_PATH.read_text(encoding="utf-8") if PERSONA_PATH.is_file() else ""
    system = (
        f"{persona}\n\n"
        "Tu as des outils — UTILISE-LES pour agir (lire repo, patch vault, powershell, journal). "
        "Ne demande pas à Sylvain de lancer des commandes. Réponse finale courte en français."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    fns = _load_tool_fns()

    trace("moteur", f"Cloud tools — {provider}/{model}")
    for step in range(1, MAX_STEPS + 1):
        with StepTimer(f"LLM cloud étape {step}/{MAX_STEPS}"):
            data = _chat(url, api_key, model, messages, tools=True)
        choice = data["choices"][0]["message"]
        tool_calls = choice.get("tool_calls") or []
        if choice.get("content"):
            trace_block("pensee", f"modèle (étape {step})", choice["content"], max_lines=6)

        if not tool_calls:
            content = (choice.get("content") or "").strip()
            if content:
                return content
            return "(aucune réponse)"

        messages.append(choice)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            trace("outil", f"appel {name} ← {str(raw_args)[:180]}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            result = _run_tool(fns, name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id") or name,
                    "content": result[:8000],
                }
            )

    return "Limite d'étapes outils atteinte — relance avec une demande plus ciblée."


def provider_label() -> str:
    p, _, _, m = _resolve_llm()
    return f"{p}/{m}"