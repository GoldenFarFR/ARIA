"""Client REST minimal pour Letta 0.6.7 (évite incompatibilités SDK)."""
from __future__ import annotations

import json
import requests

from aria_config import DEFAULT_MEMORY_BLOCKS, LETTA_URL

SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})


def list_agents() -> list[dict]:
    r = SESSION.get(f"{LETTA_URL}/v1/agents/", timeout=30)
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


def send_message(agent_id: str, message: str) -> str | None:
    body = {"messages": [{"role": "user", "text": message}]}
    r = SESSION.post(f"{LETTA_URL}/v1/agents/{agent_id}/messages", json=body, timeout=600)
    r.raise_for_status()
    data = r.json()
    texts: list[str] = []
    for m in data.get("messages", []):
        mtype = m.get("message_type")
        if mtype == "assistant_message":
            chunk = m.get("text") or m.get("content") or ""
            if chunk:
                texts.append(chunk)
        elif mtype == "tool_call_message":
            tool = m.get("tool_call") or {}
            if tool.get("name") == "send_message" and tool.get("arguments"):
                try:
                    args = json.loads(tool["arguments"])
                    msg = args.get("message") or args.get("text") or ""
                    if msg:
                        texts.append(msg)
                except json.JSONDecodeError:
                    pass
    return "\n".join(texts).strip() or None