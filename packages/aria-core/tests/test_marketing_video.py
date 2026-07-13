"""Vidéo marketing à partir d'un verdict /vc déjà capturé (tâche #23) -- gate OFF
par défaut, aucun recalcul, ne publie jamais rien elle-même."""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_core.skills import marketing_video as mv


def _snapshot(**overrides) -> dict:
    base = {
        "id": 1,
        "contract": "0xABCDEF",
        "symbol": "TEST",
        "these": "These de test",
        "cible": "0.05",
        "invalidation": "0.01",
        "scenarios": [{"nom": "bull", "cible": "0.10", "probabilite": 0.2}],
        "chart_data_uri": "",
    }
    base.update(overrides)
    return base


def test_strip_ai_trace_removes_em_dash_and_emoji():
    text = "Une these solide — avec un rocket 🚀 et un tiret court – aussi"
    cleaned = mv.strip_ai_trace(text)
    assert "—" not in cleaned
    assert "–" not in cleaned
    assert "🚀" not in cleaned


def test_strip_ai_trace_empty_string():
    assert mv.strip_ai_trace("") == ""


def test_marketing_video_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_MARKETING_VIDEO_ENABLED", raising=False)
    assert mv.marketing_video_enabled() is False


def test_marketing_video_enabled_with_env_flag(monkeypatch):
    monkeypatch.setenv("ARIA_MARKETING_VIDEO_ENABLED", "1")
    assert mv.marketing_video_enabled() is True


def test_render_video_frames_creates_files_no_network(tmp_path):
    frames = mv.render_video_frames(_snapshot(), out_dir=tmp_path)
    assert len(frames) >= 4
    for frame in frames:
        assert frame.exists()
        assert frame.suffix == ".png"


def test_render_video_frames_strips_ai_trace_from_thesis(tmp_path):
    frames = mv.render_video_frames(
        _snapshot(these="These avec emoji 🚀 et tiret —"), out_dir=tmp_path
    )
    # Rendu déterministe, offline -- aucune assertion réseau nécessaire ; on vérifie
    # juste que le rendu ne casse pas sur un texte à nettoyer.
    assert all(frame.exists() for frame in frames)


def test_assemble_video_uses_list_args_never_shell(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

        class _Result:
            returncode = 0

        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return _Result()

    monkeypatch.setattr(mv.subprocess, "run", fake_run)

    frame = tmp_path / "frame_000.png"
    frame.write_bytes(b"fake-png")
    out_path = tmp_path / "out.mp4"

    result = mv.assemble_video([frame], out_path=out_path)

    assert result == out_path
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert isinstance(cmd, list)
    assert cmd[0] == "ffmpeg"
    assert kwargs.get("shell", False) is False
    # concat-demuxer : le seul chemin variable dans la commande est le fichier de
    # concat généré en interne (liste de frames PNG déjà rendues) et out_path --
    # jamais une chaîne assemblée depuis une donnée externe (contract/thèse/LLM).
    assert "-f" in cmd and "concat" in cmd
    assert str(out_path) in cmd


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_MARKETING_VIDEO_ENABLED", raising=False)
    result = await mv.run_marketing_video_cycle()
    assert result["outcome"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_cycle_nothing_new_when_queue_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_MARKETING_VIDEO_ENABLED", "1")
    monkeypatch.setattr("aria_core.paths.aria_marketing_video_dir", lambda: tmp_path)

    async def _empty():
        return None

    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.load_next_video_candidate", _empty
    )

    result = await mv.run_marketing_video_cycle()
    assert result["outcome"] == "nothing_new"


@pytest.mark.asyncio
async def test_cycle_generates_video_and_creates_approval_never_publishes(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_MARKETING_VIDEO_ENABLED", "1")
    monkeypatch.setattr("aria_core.paths.aria_marketing_video_dir", lambda: tmp_path)

    snapshot = _snapshot()

    async def _load():
        return snapshot

    marked = []

    async def _mark(candidate_id, *, status="done"):
        marked.append((candidate_id, status))

    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.load_next_video_candidate", _load
    )
    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.mark_video_candidate_done", _mark
    )
    monkeypatch.setattr(
        mv, "render_video_frames", lambda snap, out_dir: [out_dir / "frame_000.png"]
    )

    def _fake_assemble(frames, *, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-mp4")
        return out_path

    monkeypatch.setattr(mv, "assemble_video", _fake_assemble)

    approvals_created = []

    class _FakeApproval:
        id = "abc123"

    async def _fake_create_approval(action, description, *, payload="{}", requested_by="aria"):
        approvals_created.append(
            {"action": action, "description": description, "payload": payload}
        )
        return _FakeApproval()

    monkeypatch.setattr("aria_core.approvals.create_approval", _fake_create_approval)

    notified = []

    async def notifier(text):
        notified.append(text)

    result = await mv.run_marketing_video_cycle(notifier=notifier)

    assert result["outcome"] == "ok"
    assert result["approval_id"] == "abc123"
    assert len(approvals_created) == 1
    assert approvals_created[0]["action"] == "publish_marketing_video"
    assert marked == [(1, "ready_for_review")]
    assert notified


@pytest.mark.asyncio
async def test_cycle_render_error_marks_candidate_error_no_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_MARKETING_VIDEO_ENABLED", "1")
    monkeypatch.setattr("aria_core.paths.aria_marketing_video_dir", lambda: tmp_path)

    snapshot = _snapshot()

    async def _load():
        return snapshot

    marked = []

    async def _mark(candidate_id, *, status="done"):
        marked.append((candidate_id, status))

    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.load_next_video_candidate", _load
    )
    monkeypatch.setattr(
        "aria_core.skills.vc_session_context.mark_video_candidate_done", _mark
    )

    def _boom(snap, out_dir):
        raise RuntimeError("rendu casse")

    monkeypatch.setattr(mv, "render_video_frames", _boom)

    approvals_created = []

    async def _fake_create_approval(action, description, *, payload="{}", requested_by="aria"):
        approvals_created.append(action)

    monkeypatch.setattr("aria_core.approvals.create_approval", _fake_create_approval)

    result = await mv.run_marketing_video_cycle()

    assert result["outcome"] == "error"
    assert marked == [(1, "error")]
    assert approvals_created == []


def test_module_never_imports_vc_analysis_no_recompute():
    """Garde-fou statique : ce module ne doit jamais recalculer un verdict --
    aucune dépendance vers vc_analysis (le module qui fait le scan + appel LLM)."""
    import inspect

    source = inspect.getsource(mv)
    assert "vc_analysis" not in source
    assert "analyze_vc" not in source


def test_module_never_imports_publishing_code():
    """Garde-fou statique : ce module ne publie jamais rien lui-même -- s'arrête
    à approvals.create_approval. Vérifie les imports réels (pas les mentions en
    commentaire/docstring, qui référencent volontairement ces modules en prose)."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mv))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)

    forbidden_substrings = ("tiktok", "x_twitter", "release_pipeline")
    for module in imported_modules:
        for forbidden in forbidden_substrings:
            assert forbidden not in module, f"import interdit détecté: {module}"
