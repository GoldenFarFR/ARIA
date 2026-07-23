"""Periodic consolidation of operational memory — #128.

Design: aria-ops/docs/aria-learning-inbox/2026-07-12-design-memory-consolidation.md
(mem0/Zep/Letta comparison + real inspection of ARIA's volumes).

Non-negotiable guardrail (hard-locked, never bypassed):
  - AUTHORIZED scope, only:
      * ``memory_dir()`` — ``{category}_{date}.md`` files (episodic log).
      * ``cognitive_knowledge`` WHERE ``approved = 0`` (empty today — 0 rows as of
        12/07 — but the scope is locked from the start to stay correct
        once this table starts accumulating unapproved entries).
        Not exercised by the algorithm below for now (nothing to consolidate), the
        lock exists to prevent a future extension from widening the scope by
        mistake.
  - FORBIDDEN, hard-locked — this module must NEVER import or call:
      * ``aria_core.paths.truth_ledger_dir`` / any read-write of the truth-ledger
        (the `supersedes` succession mechanism is already handled elsewhere).
      * ``aria_core.knowledge.cognitive.get_approved`` / ``approve_knowledge`` /
        ``upsert_knowledge_by_topic`` / ``build_context_summary`` (``approved = 1``
        entries — already consolidated by construction, confidence=1.0).
      * ``aria_core.memory.values`` / ``aria_core.memory.goals`` (hand-curated
        identity, out of scope by nature).
    ``_assert_not_truth_ledger`` explicitly fails (fail-closed) any
    write attempt that would land under ``truth_ledger_dir()`` — a safety net
    on top of the import discipline above (cf. test_memory_consolidation.py which
    statically verifies the absence of forbidden symbols in this file).

Never physical deletion — archive-then-rewrite: before any rewrite, a
raw snapshot of the touched entries is written to
``memory_dir()/archive/consolidated_{date}.jsonl`` (one JSON line per entry,
``{category, date, content, source_file}``). This snapshot is never itself
consolidated or pruned — a recovery net in case of a merge error. Only the
source files ``memory_dir()/{category}_{date}.md`` are marked consolidated (separate
registry, never deleted nor rewritten).

A single LLM call per active category (not one per entry — that's the main cost
lever), explicitly routed at ``depth="brief"`` — never ``"develop"`` for a routine
housekeeping task. Volume threshold (``_MIN_NEW_ENTRIES``): a category is only
consolidated if at least this many new entries have accumulated since the
last pass, to avoid daily LLM calls on inactive categories.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.llm import chat_with_context
from aria_core.paths import memory_dir, truth_ledger_dir

logger = logging.getLogger(__name__)

_MIN_NEW_ENTRIES = 3
_MAX_TOKENS = 400

_CATEGORY_RE = re.compile(r"^(?P<category>[a-z0-9_]+)_(?P<date>\d{4}-\d{2}-\d{2})\.md$")

_CONSOLIDATION_SYSTEM = """Tu consolides la mémoire opérationnelle d'ARIA pour UNE catégorie.

Règles strictes (dans l'ordre de priorité) :
1. NE JAMAIS reformuler au point de perdre un fait précis (nombre, adresse, décision,
   date). L'exactitude prime toujours sur la concision — en cas de doute, garde le détail.
2. Sépare le durable (préférence, pattern récurrent, décision qui reste vraie) du daté
   (événement ponctuel dont la date est passée) — retire le daté périmé, ou replie-le en
   un takeaway durable s'il garde une valeur.
3. Fusionne les entrées qui se recoupent en gardant le contenu le plus riche/précis.
4. Retire ce qui est trivialement re-dérivable (ex. un statut répété identique dix fois
   → une seule ligne "stable depuis le X").

Réponds UNIQUEMENT avec le nouveau contenu consolidé de la catégorie, en Markdown,
sans préambule ni méta-commentaire sur ce que tu as fait."""


def consolidation_enabled() -> bool:
    """Gate OFF by default — same discipline as the other sensitive heartbeat tasks."""
    import os

    return os.environ.get("ARIA_MEMORY_CONSOLIDATION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _assert_not_truth_ledger(path: Path) -> None:
    """Fail-closed: this module must NEVER write under truth_ledger_dir()."""
    forbidden = truth_ledger_dir().resolve()
    resolved = path.resolve()
    if resolved == forbidden or forbidden in resolved.parents:
        raise RuntimeError(
            f"memory/consolidation.py: write refused under the truth-ledger ({path})"
        )


def _consolidated_dir() -> Path:
    return memory_dir() / "consolidated"


def _consolidated_path(category: str) -> Path:
    return _consolidated_dir() / f"{category}.md"


def _archive_dir() -> Path:
    return memory_dir() / "archive"


def _registry_path() -> Path:
    return _consolidated_dir() / "_registry.json"


def _load_registry() -> dict[str, list[str]]:
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): [str(v) for v in vs]
        for k, vs in raw.items()
        if isinstance(vs, list)
    }


def _save_registry(registry: dict[str, list[str]]) -> None:
    path = _registry_path()
    _assert_not_truth_ledger(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _dated_files_by_category() -> dict[str, list[Path]]:
    """`{category}_{date}.md` files from memory_dir(), grouped by category.

    Deliberately ignores anything that doesn't match this exact name (`.jsonl` of
    the arbitrator/reflection, state `.json`, `training_portfolio.md` without a date, the
    `consolidated/` and `archive/` subdirectories — non-recursive by the nature of
    `Path.glob("*.md")`)."""
    out: dict[str, list[Path]] = defaultdict(list)
    for path in memory_dir().glob("*.md"):
        match = _CATEGORY_RE.match(path.name)
        if not match:
            continue
        out[match.group("category")].append(path)
    for files in out.values():
        files.sort(key=lambda p: p.name)  # date at the start of the name -> chronological sort
    return out


def _date_from_filename(name: str) -> str:
    match = _CATEGORY_RE.match(name)
    return match.group("date") if match else ""


def _parse_entries(path: Path) -> list[dict[str, str]]:
    """Splits a `{category}_{date}.md` file into `## [HH:MM:SS UTC]\\ncontent` entries."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    sections = [s.strip() for s in text.split("## [") if s.strip()]
    entries: list[dict[str, str]] = []
    for section in sections:
        if "]" not in section:
            continue
        timestamp, _, content = section.partition("]")
        content = content.strip()
        if content:
            entries.append({"timestamp": timestamp.strip(), "content": content})
    return entries


def _archive_raw(category: str, entries: list[dict[str, str]]) -> None:
    """Archive-then-rewrite: raw snapshot BEFORE any rewrite. Never pruned."""
    archive_dir = _archive_dir()
    _assert_not_truth_ledger(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = archive_dir / f"consolidated_{today}.jsonl"
    _assert_not_truth_ledger(archive_path)
    with archive_path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(
                json.dumps(
                    {
                        "category": category,
                        "date": entry["date"],
                        "content": entry["content"],
                        "source_file": entry["source_file"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


async def _consolidate_category(
    category: str, existing: str, new_entries: list[dict[str, str]]
) -> str | None:
    entries_block = "\n".join(
        f"- [{e['date']} {e['timestamp']}] {e['content']}" for e in new_entries
    )
    user_message = (
        f"Catégorie : {category}\n\n"
        f"Contenu déjà consolidé (vide au premier passage) :\n{existing.strip() or '(vide)'}\n\n"
        f"Nouvelles entrées brutes à intégrer :\n{entries_block}\n\n"
        "Produis le nouveau contenu consolidé COMPLET pour cette catégorie "
        "(reprend ce qui reste valable du contenu déjà consolidé + les nouvelles entrées)."
    )
    return await chat_with_context(
        user_message,
        _CONSOLIDATION_SYSTEM,
        max_tokens=_MAX_TOKENS,
        depth="brief",
    )


async def run_memory_consolidation_cycle(
    *, min_new_entries: int = _MIN_NEW_ENTRIES
) -> dict[str, Any]:
    """Heartbeat entry point. Gated OFF by default (cf. `consolidation_enabled`)."""
    if not consolidation_enabled():
        return {"outcome": "disabled"}

    by_category = _dated_files_by_category()
    registry = _load_registry()
    consolidated: list[str] = []
    skipped_below_threshold: list[str] = []
    failed: list[str] = []

    for category in sorted(by_category):
        files = by_category[category]
        done = set(registry.get(category, []))
        pending_files = [f for f in files if f.name not in done]
        if not pending_files:
            continue

        new_entries: list[dict[str, str]] = []
        for path in pending_files:
            date = _date_from_filename(path.name)
            for entry in _parse_entries(path):
                new_entries.append({**entry, "date": date, "source_file": path.name})

        if len(new_entries) < min_new_entries:
            skipped_below_threshold.append(category)
            continue

        # Archive-then-rewrite: the raw data is saved BEFORE any LLM call/rewrite.
        # If the LLM then fails, nothing is lost and the registry doesn't advance -- the
        # category will be retried next pass (the archive may then contain a
        # duplicate of these entries, with no consequence: it is never pruned nor read back
        # as a source of truth, only a recovery net).
        _archive_raw(category, new_entries)

        consolidated_path = _consolidated_path(category)
        existing = (
            consolidated_path.read_text(encoding="utf-8")
            if consolidated_path.is_file()
            else ""
        )

        try:
            new_content = await _consolidate_category(category, existing, new_entries)
        except Exception as exc:
            logger.warning("memory_consolidation: failed category=%s: %s", category, exc)
            failed.append(category)
            continue

        if not new_content or not new_content.strip():
            # LLM unavailable/empty -> graceful degradation, nothing lost (already archived),
            # retried next pass.
            failed.append(category)
            continue

        _assert_not_truth_ledger(consolidated_path)
        consolidated_path.parent.mkdir(parents=True, exist_ok=True)
        consolidated_path.write_text(new_content.strip() + "\n", encoding="utf-8")

        registry.setdefault(category, [])
        registry[category].extend(f.name for f in pending_files)
        consolidated.append(category)

    if consolidated:
        _save_registry(registry)

    return {
        "outcome": "ok" if consolidated else "no_op",
        "consolidated": consolidated,
        "skipped_below_threshold": skipped_below_threshold,
        "failed": failed,
    }
