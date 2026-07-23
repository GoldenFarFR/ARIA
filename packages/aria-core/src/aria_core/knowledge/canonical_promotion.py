"""Promotion to canonical facts — proposals awaiting operator approval."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aria_core.paths import data_dir

QUEUE_PATH = data_dir() / "canonical_promotions.json"


def _load() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(items[-50:], ensure_ascii=False, indent=2), encoding="utf-8")


def queue_promotion(
    claim: str,
    *,
    source: str = "calibrate",
    p_true: float = 0.9,
    verdict: str = "vrai",
) -> dict:
    item = {
        "id": str(uuid4())[:8],
        "at": datetime.now(timezone.utc).isoformat(),
        "claim": claim[:400],
        "source": source[:80],
        "p_true": round(p_true, 3),
        "verdict": verdict,
        "status": "pending",
    }
    items = _load()
    items.append(item)
    _save(items)
    return item


def format_pending_promotion(item: dict, lang: str = "fr") -> str:
    if lang == "fr":
        return (
            f"Promotion canonique proposée [{item['id']}] :\n"
            f"« {item['claim'][:200]} »\n"
            f"Source : {item.get('source', '?')} · P(vrai)={item.get('p_true', 0)}\n"
            f"Valide avec /learn epistemic | {item['claim'][:120]}"
        )
    return (
        f"Canonical promotion proposed [{item['id']}]:\n"
        f"« {item['claim'][:200]} »\n"
        f"Source: {item.get('source', '?')} · P(true)={item.get('p_true', 0)}\n"
        f"Approve via /learn epistemic | {item['claim'][:120]}"
    )