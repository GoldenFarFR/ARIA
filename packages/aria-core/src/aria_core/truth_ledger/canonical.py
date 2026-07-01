"""Sync canonical_facts.yaml → Truth Ledger (supersedes stale entries)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aria_core.truth_ledger.store import (
    supersede_canonical_id,
    upsert_canonical_entry,
)

_CANONICAL_PATH = Path(__file__).parent / "canonical_facts.yaml"


def load_canonical_facts() -> list[dict]:
    if not _CANONICAL_PATH.exists():
        return []
    raw = yaml.safe_load(_CANONICAL_PATH.read_text(encoding="utf-8")) or []
    return raw if isinstance(raw, list) else []


def _answer_hash(answer: str) -> str:
    return hashlib.sha256(answer.strip().encode()).hexdigest()[:12]


async def sync_canonical_facts() -> dict:
    """Load YAML, supersede changed facts, insert new verified canonical entries."""
    facts = load_canonical_facts()
    synced = 0
    superseded = 0
    unchanged = 0

    for fact in facts:
        cid = fact.get("id", "").strip()
        if not cid:
            continue
        question = (fact.get("question") or "").strip()
        answer = (fact.get("answer") or "").strip()
        topic = (fact.get("topic") or cid).strip()
        if not question or not answer:
            continue

        prev_hash = await _get_active_canonical_hash(cid)
        new_hash = _answer_hash(answer)
        if prev_hash == new_hash:
            unchanged += 1
            continue

        old_ids = await supersede_canonical_id(cid)
        superseded += len(old_ids)

        await upsert_canonical_entry(
            canonical_id=cid,
            topic=topic,
            question=question,
            answer=answer,
            tags=fact.get("tags") or [],
            supersedes=old_ids,
        )
        synced += 1

    # Refresh faq.yaml from canonical (FAQ skill uses same truths)
    _export_faq_from_canonical(facts)

    return {
        "canonical_file": str(_CANONICAL_PATH),
        "synced": synced,
        "superseded": superseded,
        "unchanged": unchanged,
        "total_facts": len(facts),
    }


async def _get_active_canonical_hash(canonical_id: str) -> str | None:
    from aria_core.truth_ledger.store import get_active_canonical_hash
    return await get_active_canonical_hash(canonical_id)


def _export_faq_from_canonical(facts: list[dict]) -> None:
    """Keep faq.yaml aligned with canonical facts."""
    faq_path = Path(__file__).parent.parent / "content" / "faq.yaml"
    export = []
    for fact in facts:
        export.append({
            "id": fact.get("id"),
            "tags": fact.get("tags") or [],
            "question": fact.get("question", "").strip(),
            "answer": fact.get("answer", "").strip(),
        })
    faq_path.write_text(
        yaml.dump(export, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    # Bust FAQ cache
    from aria_core.content import service as content_service
    content_service._FAQ_CACHE = None