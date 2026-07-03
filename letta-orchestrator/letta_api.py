"""Client REST minimal pour Letta 0.6.7 (évite incompatibilités SDK)."""
from __future__ import annotations

import json
import requests

from aria_config import DEFAULT_MEMORY_BLOCKS, LETTA_URL, OLLAMA_NUM_CTX

SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})


def list_agents() -> list[dict]:
    r = SESSION.get(f"{LETTA_URL}/v1/agents/", timeout=30)
    r.raise_for_status()
    return r.json()


def parse_llm_handle(handle: str) -> dict:
    """Convertit 'ollama/qwen2.5:14b' en llm_config Letta 0.6.7."""
    if "/" in handle:
        endpoint, model = handle.split("/", 1)
    else:
        endpoint, model = "ollama", handle
    cfg = {
        "model": model,
        "model_endpoint_type": endpoint,
        "model_wrapper": "chatml",
        "context_window": OLLAMA_NUM_CTX,
        "put_inner_thoughts_in_kwargs": True,
        "handle": handle,
    }
    if endpoint == "ollama":
        from aria_config import OLLAMA_BASE_URL

        cfg["model_endpoint"] = OLLAMA_BASE_URL
    elif endpoint == "groq":
        cfg["model_endpoint"] = "https://api.groq.com/openai/v1"
    return cfg


def update_agent(agent_id: str, llm_handle: str) -> dict:
    body = {"llm_config": parse_llm_handle(llm_handle)}
    r = SESSION.patch(f"{LETTA_URL}/v1/agents/{agent_id}", json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def create_agent(name: str, llm: str, embedding: str, description: str) -> str:
    body = {
        "name": name,
        "llm": llm,
        "embedding": embedding,
        "description": description,
        "memory_blocks": DEFAULT_MEMORY_BLOCKS,
    }
    r = SESSION.post(f"{LETTA_URL}/v1/agents/", json=body, timeout=300)
    r.raise_for_status()
    return r.json()["id"]


def _tool_call_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_user_text(m: dict) -> str:
    mtype = m.get("message_type")
    if mtype == "assistant_message":
        return (
            m.get("assistant_message")
            or m.get("text")
            or m.get("content")
            or ""
        ).strip()
    if mtype == "tool_call_message":
        tool = m.get("tool_call") or {}
        if tool.get("name") == "send_message":
            args = _tool_call_args(tool.get("arguments"))
            return (args.get("message") or args.get("text") or "").strip()
    return ""


def send_message(agent_id: str, message: str) -> str | None:
    body = {"messages": [{"role": "user", "text": message}]}
    r = SESSION.post(f"{LETTA_URL}/v1/agents/{agent_id}/messages", json=body, timeout=600)
    r.raise_for_status()
    data = r.json()
    texts: list[str] = []
    for m in data.get("messages", []):
        chunk = _extract_user_text(m)
        if chunk:
            texts.append(chunk)
    return "\n".join(texts).strip() or None


def get_tool_id_by_name(name: str) -> str | None:
    r = SESSION.get(f"{LETTA_URL}/v1/tools/name/{name}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text.strip().strip('"')


def upsert_tool(name: str, source_code: str, description: str) -> str:
    existing = get_tool_id_by_name(name)
    body = {
        "name": name,
        "description": description,
        "source_code": source_code,
        "source_type": "python",
    }
    if existing:
        r = SESSION.patch(f"{LETTA_URL}/v1/tools/{existing}", json=body, timeout=120)
        r.raise_for_status()
        return existing
    r = SESSION.post(f"{LETTA_URL}/v1/tools/", json=body, timeout=120)
    r.raise_for_status()
    return r.json()["id"]


def add_tool_to_agent(agent_id: str, tool_id: str) -> None:
    r = SESSION.patch(
        f"{LETTA_URL}/v1/agents/{agent_id}/add-tool/{tool_id}",
        timeout=60,
    )
    r.raise_for_status()


def update_agent_memory_block(agent_id: str, block_label: str, value: str) -> None:
    body = {"value": value[:12000]}
    r = SESSION.patch(
        f"{LETTA_URL}/v1/agents/{agent_id}/memory/block/{block_label}",
        json=body,
        timeout=60,
    )
    r.raise_for_status()


def create_ouvrier_agent(
    name: str,
    llm: str,
    embedding: str,
    persona: str,
    tool_ids: list[str],
) -> str:
    body = {
        "name": name,
        "llm": llm,
        "embedding": embedding,
        "description": "Ouvrier ARIA — copie conforme Cursor/Grok (outils repo)",
        "memory_blocks": [
            {
                "label": "persona",
                "value": persona[:8000],
                "description": "Règles ouvrier ARIA",
            },
            {
                "label": "human",
                "value": "Opérateur : Sylvain Rio (GoldenFarFR). Monorepo : ARIA.",
                "description": "Contexte humain",
            },
        ],
        "tool_ids": tool_ids,
    }
    r = SESSION.post(f"{LETTA_URL}/v1/agents/", json=body, timeout=300)
    r.raise_for_status()
    return r.json()["id"]