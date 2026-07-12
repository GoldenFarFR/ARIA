"""Consolidation périodique de la mémoire opérationnelle — #128.

Design : aria-ops/docs/aria-learning-inbox/2026-07-12-design-memory-consolidation.md
(comparaison mem0/Zep/Letta + inspection réelle des volumes ARIA).

Garde-fou non négociable (verrouillé en dur, jamais contourné) :
  - Périmètre AUTORISÉ, uniquement :
      * ``memory_dir()`` — fichiers ``{category}_{date}.md`` (journal épisodique).
      * ``cognitive_knowledge`` WHERE ``approved = 0`` (aujourd'hui vide — 0 ligne au
        12/07 — mais le périmètre est verrouillé dès le départ pour rester correct
        quand cette table commencera à accumuler des entrées non approuvées).
        Non exercé par l'algorithme ci-dessous pour l'instant (rien à consolider), le
        verrou existe pour empêcher une extension future d'élargir le périmètre par
        erreur.
  - INTERDIT, verrouillé en dur — ce module ne doit JAMAIS importer ni appeler :
      * ``aria_core.paths.truth_ledger_dir`` / toute lecture-écriture du truth-ledger
        (mécanisme de succession `supersedes` déjà géré ailleurs).
      * ``aria_core.knowledge.cognitive.get_approved`` / ``approve_knowledge`` /
        ``upsert_knowledge_by_topic`` / ``build_context_summary`` (entrées
        ``approved = 1`` — déjà consolidées par construction, confidence=1.0).
      * ``aria_core.memory.values`` / ``aria_core.memory.goals`` (identité curatée à
        la main, hors périmètre par nature).
    ``_assert_not_truth_ledger`` fait échouer explicitement (fail-closed) toute
    tentative d'écriture qui atterrirait sous ``truth_ledger_dir()`` — filet de sécurité
    en plus de la discipline d'imports ci-dessus (cf. test_memory_consolidation.py qui
    vérifie statiquement l'absence des symboles interdits dans ce fichier).

Jamais de suppression physique — archive-then-rewrite : avant toute réécriture, un
instantané brut des entrées touchées est écrit dans
``memory_dir()/archive/consolidated_{date}.jsonl`` (une ligne JSON par entrée,
``{category, date, content, source_file}``). Cet instantané n'est jamais lui-même
consolidé ni élagué — filet de récupération en cas d'erreur de fusion. Seuls les
fichiers-sources ``memory_dir()/{category}_{date}.md`` sont marqués consolidés (registre
séparé, jamais supprimés ni réécrits).

Un seul appel LLM par catégorie active (pas un par entrée — c'est le principal levier de
coût), routé explicitement en ``depth="brief"`` — jamais ``"develop"`` pour une tâche de
housekeeping routinière. Seuil de volume (``_MIN_NEW_ENTRIES``) : une catégorie n'est
consolidée que si au moins ce nombre de nouvelles entrées s'est accumulé depuis le
dernier passage, pour éviter des appels LLM quotidiens sur des catégories inactives.
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
    """Gate OFF par défaut — même discipline que les autres tâches heartbeat sensibles."""
    import os

    return os.environ.get("ARIA_MEMORY_CONSOLIDATION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _assert_not_truth_ledger(path: Path) -> None:
    """Fail-closed : ce module ne doit JAMAIS écrire sous truth_ledger_dir()."""
    forbidden = truth_ledger_dir().resolve()
    resolved = path.resolve()
    if resolved == forbidden or forbidden in resolved.parents:
        raise RuntimeError(
            f"memory/consolidation.py: écriture refusée sous le truth-ledger ({path})"
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
    """Fichiers `{category}_{date}.md` de memory_dir(), groupés par catégorie.

    Ignore délibérément tout ce qui ne matche pas ce nom exact (`.jsonl` de
    l'arbitre/reflection, `.json` d'état, `training_portfolio.md` sans date, le
    sous-répertoire `consolidated/` et `archive/` — non recursif par nature de
    `Path.glob("*.md")`)."""
    out: dict[str, list[Path]] = defaultdict(list)
    for path in memory_dir().glob("*.md"):
        match = _CATEGORY_RE.match(path.name)
        if not match:
            continue
        out[match.group("category")].append(path)
    for files in out.values():
        files.sort(key=lambda p: p.name)  # date en tête de nom -> tri chronologique
    return out


def _date_from_filename(name: str) -> str:
    match = _CATEGORY_RE.match(name)
    return match.group("date") if match else ""


def _parse_entries(path: Path) -> list[dict[str, str]]:
    """Découpe un fichier `{category}_{date}.md` en entrées `## [HH:MM:SS UTC]\\ncontenu`."""
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
    """Archive-then-rewrite : instantané brut AVANT toute réécriture. Jamais élagué."""
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
    """Point d'entrée heartbeat. Gated OFF par défaut (cf. `consolidation_enabled`)."""
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

        # Archive-then-rewrite : le brut est sauvegardé AVANT tout appel LLM/réécriture.
        # Si le LLM échoue ensuite, rien n'est perdu et le registre n'avance pas -- la
        # catégorie sera retentée au prochain passage (l'archive peut alors contenir un
        # doublon de ces entrées, sans conséquence : elle n'est jamais élaguée ni relue
        # comme source de vérité, seulement un filet de récupération).
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
            logger.warning("memory_consolidation: échec catégorie=%s: %s", category, exc)
            failed.append(category)
            continue

        if not new_content or not new_content.strip():
            # LLM indisponible/vide -> dégradation gracieuse, rien perdu (déjà archivé),
            # retenté au prochain passage.
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
