"""memory/consolidation.py — #128.

Garde-fou (jamais truth-ledger, jamais cognitive_knowledge approved=1), archive-then-
rewrite (aucune suppression physique), un appel LLM par catégorie en depth="brief",
seuil de volume, câblage HeartbeatTask gated OFF par défaut."""
from __future__ import annotations

import inspect
import json

import pytest
from types import CodeType

from aria_core import heartbeat
from aria_core.memory import consolidation
from aria_core.paths import memory_dir, truth_ledger_dir
from aria_core.testing import configure_test_runtime


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    yield


def _write_entries(category: str, date: str, contents: list[str]):
    path = memory_dir() / f"{category}_{date}.md"
    lines = [f"# ARIA memory — {category} — {date}\n"]
    for i, content in enumerate(contents):
        lines.append(f"\n## [{10 + i:02d}:00:00 UTC]\n{content}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


# ── garde-fou (statique + runtime) ─────────────────────────────────────────────────────


def _all_code_names(code: CodeType) -> set[str]:
    """Noms référencés par le BYTECODE (appels, attributs) -- ignore les docstrings et
    commentaires (simples constantes chaîne, jamais dans co_names)."""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, CodeType):
            names |= _all_code_names(const)
    return names


def test_module_never_calls_forbidden_symbols():
    """Verrou en dur, testable au niveau bytecode (pas un simple grep texte) : ce module
    ne doit jamais APPELER les symboles interdits (truth-ledger, cognitive_knowledge
    approved=1, memory/values.py, memory/goals.py)."""
    names: set[str] = set()
    for obj in vars(consolidation).values():
        if inspect.isfunction(obj) and obj.__module__ == consolidation.__name__:
            names |= _all_code_names(obj.__code__)
    forbidden = {
        "get_approved",
        "approve_knowledge",
        "upsert_knowledge_by_topic",
        "build_context_summary",
        "get_values_text",
        "get_goals_text",
    }
    hit = names & forbidden
    assert not hit, f"symbole(s) interdit(s) appelé(s) dans consolidation.py : {hit}"


def test_assert_not_truth_ledger_blocks_write():
    with pytest.raises(RuntimeError):
        consolidation._assert_not_truth_ledger(truth_ledger_dir() / "canary.md")
    with pytest.raises(RuntimeError):
        consolidation._assert_not_truth_ledger(truth_ledger_dir() / "sub" / "canary.md")


def test_assert_not_truth_ledger_allows_memory_dir():
    consolidation._assert_not_truth_ledger(memory_dir() / "consolidated" / "vc.md")  # no raise


# ── gate OFF par défaut ─────────────────────────────────────────────────────────────────


def test_consolidation_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", raising=False)
    assert consolidation.consolidation_enabled() is False


def test_consolidation_env_toggle(monkeypatch):
    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    assert consolidation.consolidation_enabled() is True


@pytest.mark.asyncio
async def test_run_cycle_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", raising=False)
    _write_entries("vc", "2026-07-10", ["a", "b", "c"])
    result = await consolidation.run_memory_consolidation_cycle()
    assert result == {"outcome": "disabled"}
    # rien écrit -- aucun appel LLM, aucun fichier consolidé/archive créé
    assert not (memory_dir() / "consolidated").exists()
    assert not (memory_dir() / "archive").exists()


# ── enumeration des catégories ──────────────────────────────────────────────────────────


def test_dated_files_grouped_by_category_ignores_non_matching(tmp_path):
    _write_entries("vc", "2026-07-10", ["a"])
    _write_entries("vc", "2026-07-11", ["b"])
    _write_entries("heartbeat", "2026-07-11", ["c"])
    (memory_dir() / "arbitration.jsonl").write_text("{}\n", encoding="utf-8")
    (memory_dir() / "training_portfolio.md").write_text("# no date suffix\n", encoding="utf-8")
    by_category = consolidation._dated_files_by_category()
    assert set(by_category["vc"]) == {
        memory_dir() / "vc_2026-07-10.md",
        memory_dir() / "vc_2026-07-11.md",
    }
    assert len(by_category["heartbeat"]) == 1
    assert "training_portfolio" not in by_category
    assert list(by_category["vc"])[0].name == "vc_2026-07-10.md"  # tri chronologique


# ── seuil de volume ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_category_below_threshold_is_skipped_no_llm_call(monkeypatch):
    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    _write_entries("vc", "2026-07-10", ["only one entry"])

    called = {"n": 0}

    async def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("LLM ne doit pas être appelé sous le seuil de volume")

    monkeypatch.setattr(consolidation, "chat_with_context", _boom)

    result = await consolidation.run_memory_consolidation_cycle()
    assert result["outcome"] == "no_op"
    assert result["skipped_below_threshold"] == ["vc"]
    assert called["n"] == 0
    assert not (memory_dir() / "archive").exists()


# ── archive-then-rewrite ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidation_archives_before_rewrite_and_never_deletes_source(monkeypatch):
    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    source_path = _write_entries("vc", "2026-07-10", ["entry one", "entry two", "entry three"])
    original_source_text = source_path.read_text(encoding="utf-8")

    captured = {}

    async def _fake_chat(user_message, system_context, *, max_tokens=None, depth=None, **kw):
        captured["user_message"] = user_message
        captured["depth"] = depth
        captured["max_tokens"] = max_tokens
        return "- entry one\n- entry two\n- entry three\n"

    monkeypatch.setattr(consolidation, "chat_with_context", _fake_chat)

    result = await consolidation.run_memory_consolidation_cycle()

    assert result["outcome"] == "ok"
    assert result["consolidated"] == ["vc"]
    assert captured["depth"] == "brief"  # jamais "develop" pour du housekeeping (design #128)

    # Source jamais supprimé ni modifié.
    assert source_path.is_file()
    assert source_path.read_text(encoding="utf-8") == original_source_text

    # Archive brute écrite, une ligne JSON par entrée.
    archive_files = list((memory_dir() / "archive").glob("consolidated_*.jsonl"))
    assert len(archive_files) == 1
    lines = [json.loads(l) for l in archive_files[0].read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert {l["content"] for l in lines} == {"entry one", "entry two", "entry three"}
    assert all(l["category"] == "vc" and l["source_file"] == "vc_2026-07-10.md" for l in lines)

    # Fichier consolidé écrit.
    consolidated_path = memory_dir() / "consolidated" / "vc.md"
    assert consolidated_path.is_file()
    assert "entry one" in consolidated_path.read_text(encoding="utf-8")

    # Registre avancé -- la même entrée ne sera pas re-proposée au LLM au prochain passage.
    registry = consolidation._load_registry()
    assert "vc_2026-07-10.md" in registry["vc"]


@pytest.mark.asyncio
async def test_already_consolidated_file_not_reprocessed(monkeypatch):
    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    _write_entries("vc", "2026-07-10", ["a", "b", "c"])

    calls = {"n": 0}

    async def _fake_chat(*_a, **_k):
        calls["n"] += 1
        return "consolidated content\n"

    monkeypatch.setattr(consolidation, "chat_with_context", _fake_chat)

    first = await consolidation.run_memory_consolidation_cycle()
    assert first["consolidated"] == ["vc"]
    assert calls["n"] == 1

    # Deuxième passage, aucune nouvelle entrée -> pas de nouvel appel LLM.
    second = await consolidation.run_memory_consolidation_cycle()
    assert second["outcome"] == "no_op"
    assert calls["n"] == 1

    # Une nouvelle journée, sous le seuil -> toujours pas d'appel.
    _write_entries("vc", "2026-07-11", ["d"])
    third = await consolidation.run_memory_consolidation_cycle()
    assert third["skipped_below_threshold"] == ["vc"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_llm_failure_keeps_archive_and_retries_next_pass(monkeypatch):
    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    _write_entries("epistemic", "2026-07-10", ["x", "y", "z"])

    async def _fake_chat(*_a, **_k):
        return None  # LLM indisponible

    monkeypatch.setattr(consolidation, "chat_with_context", _fake_chat)

    result = await consolidation.run_memory_consolidation_cycle()
    assert result["outcome"] == "no_op"
    assert result["failed"] == ["epistemic"]

    # Rien perdu : l'archive existe malgré l'échec LLM.
    archive_files = list((memory_dir() / "archive").glob("consolidated_*.jsonl"))
    assert len(archive_files) == 1

    # Le registre n'a PAS avancé -- la catégorie sera retentée au prochain passage.
    registry = consolidation._load_registry()
    assert "epistemic" not in registry


# ── câblage heartbeat ────────────────────────────────────────────────────────────────────


def test_memory_consolidation_task_registered_and_off_by_default():
    task = _task("memory_consolidation")
    assert task.enabled is False
    assert task.interval_minutes == 1440


def test_memory_consolidation_gate_respects_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("memory_consolidation").enabled is False

    monkeypatch.setenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("memory_consolidation").enabled is True

    monkeypatch.delenv("ARIA_MEMORY_CONSOLIDATION_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("memory_consolidation").enabled is False


@pytest.mark.asyncio
async def test_heartbeat_dispatch_calls_consolidation_and_logs(monkeypatch):
    from aria_core.heartbeat import AriaHeartbeat

    async def _fake_cycle():
        return {"outcome": "ok", "consolidated": ["vc"], "skipped_below_threshold": [], "failed": []}

    monkeypatch.setattr(
        "aria_core.memory.consolidation.run_memory_consolidation_cycle", _fake_cycle
    )
    logged = {}
    monkeypatch.setattr(
        "aria_core.heartbeat.append_memory",
        lambda category, content: logged.setdefault(category, []).append(content),
    )

    hb = AriaHeartbeat()
    await hb._run_task("memory_consolidation")

    assert logged.get("heartbeat")
    assert "memory_consolidation" in logged["heartbeat"][0]
    assert "vc" in logged["heartbeat"][0]
