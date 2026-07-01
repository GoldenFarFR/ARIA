"""
ARIA Memory Skill - Gestion propre des mémoires court/moyen/long terme
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

MEMORY_DIR = Path(r"C:\Users\Studi\GitHub-Repos\aria-skills\core\memory")
ALLOWED = {"short", "medium", "long"}
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

def _path(mem_type: str) -> Path:
    if mem_type not in ALLOWED:
        raise ValueError(f"Type de mémoire invalide: {mem_type}")
    return MEMORY_DIR / f"{mem_type}_term_memory.json"

def _atomic_write(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def manage_memory(
    action: Literal["read", "write", "append", "list"],
    memory_type: Literal["short", "medium", "long"],
    data: Optional[dict] = None,
    key: Optional[str] = None
) -> dict:
    path = _path(memory_type)

    if action == "read":
        if not path.exists():
            return {"status": "empty", "message": f"Aucune mémoire {memory_type}."}
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        return {"status": "success", "data": content.get(key) if key else content}

    if action == "write":
        if data is None:
            return {"status": "error", "message": "Le paramètre 'data' est obligatoire"}
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, data)
        return {"status": "success", "message": f"Mémoire {memory_type} mise à jour."}

    if action == "append":
        if data is None:
            return {"status": "error", "message": "'data' obligatoire pour append"}
        existing = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        if memory_type == "short" and "recent_facts" not in existing:
            existing["recent_facts"] = []
        if memory_type == "short":
            existing.setdefault("recent_facts", []).append(data)
            existing["recent_facts"] = existing["recent_facts"][-40:]
        existing["last_updated"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, existing)
        return {"status": "success", "message": f"Données ajoutées à {memory_type}."}

    if action == "list":
        if not path.exists():
            return {"status": "success", "keys": []}
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        return {"status": "success", "keys": list(content.keys())}

    return {"status": "error", "message": f"Action '{action}' non supportée."}
