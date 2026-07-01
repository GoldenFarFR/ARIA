"""Ingest Chroma + smoke super-mémoire locale (appelé par activate-vector-local.ps1)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from aria_core.memory.reflection import append_reflection
from aria_core.memory.llm_context import build_llm_context
from aria_core.memory.vector.chroma_client import reset_client_cache
from aria_core.memory.vector.chroma_store import search, store, vector_store_status
from aria_core.memory.vector.ingest import ingest_approved_item
from aria_core.testing import AriaRuntimeSettings, configure_test_runtime


def _ingest_cognitive_ids(data_dir: Path) -> list[str]:
    db = data_dir / "aria.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    try:
        cur = con.execute("SELECT id FROM cognitive_knowledge WHERE approved = 1")
        return [r[0] for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


async def main(data_dir: Path) -> int:
    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=True,
            aria_ddg_search_cache=True,
            aria_memory_arbitrator=True,
            aria_public_mode=False,
        ),
    )
    reset_client_cache()

    before = vector_store_status()
    print(json.dumps({"phase": "before", **before}, ensure_ascii=False))

    ingested: list[str] = []
    for item_id in _ingest_cognitive_ids(data_dir):
        doc_id = await ingest_approved_item(item_id)
        if doc_id:
            ingested.append(item_id)

    seeds = [
        (
            "lesson",
            "Deploy Render groupe via deploy-render.ps1 — un seul redeploy, quota pipeline ~2min",
            {"topic": "ops", "source": "runbook", "confidence": "0.9"},
        ),
        (
            "lesson",
            "Mémoire vectorielle Chroma active localement — rappel sémantique dans build_llm_context",
            {"topic": "memory", "source": "operator", "confidence": "0.95"},
        ),
        (
            "reflection",
            "Phase H arbitre : directive > truth > cognitive > journal > vector",
            {"topic": "memory", "context": "phase-h", "confidence": "0.9"},
        ),
        (
            "decision",
            "Gem Crush retiré du monorepo — focus holding + Aria Market",
            {"topic": "product", "outcome": "retired", "at": "2026-07-01", "confidence": "1.0"},
        ),
    ]
    stored = 0
    for entry_type, content, meta in seeds:
        if await store(entry_type, content, metadata=meta):
            stored += 1

    append_reflection(
        "Vector local actif — super mémoire ARIA opérationnelle",
        context="memory",
        outcome="activated",
    )

    hits = await search("mémoire vectorielle Chroma deploy", limit=5)
    ctx = await build_llm_context(public=False, query_hint="mémoire vectorielle Chroma ARIA local")

    after = vector_store_status()
    report = {
        "phase": "after",
        "ingested_cognitive": len(ingested),
        "stored_seeds": stored,
        "search_hits": len(hits),
        "llm_vector_recall": "Rappel sémantique" in ctx,
        "llm_arbitrator": "Arbitre mémoire ARIA" in ctx,
        "llm_reflection": "Réflexion opérationnelle" in ctx,
        **after,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["llm_vector_recall"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(Path(args.data_dir))))