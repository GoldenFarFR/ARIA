import json

import pytest

from aria_core.skills import ingest_repo_skill
from aria_core.skills.ingest_repo_skill import (
    _resolve_repo_path,
    execute_ingest_repo,
    wants_ingest_repo,
)
from aria_core.testing import AriaRuntimeSettings, configure_test_runtime


@pytest.mark.asyncio
async def test_wants_ingest_repo_natural_language():
    assert wants_ingest_repo(
        "je veux que tu abordes toutes les données sur ARIA et alimentes ta memoire"
    )
    assert wants_ingest_repo("ingest-repo C:\\Users\\Studi\\GitHub-Repos\\ARIA")
    assert not wants_ingest_repo("salut comment vas tu")


@pytest.mark.asyncio
async def test_execute_ingest_repo_writes_proof(tmp_path, monkeypatch):
    repo = tmp_path / "ARIA"
    (repo / "collegue-memoire").mkdir(parents=True)
    (repo / "collegue-memoire" / "COLLEGUE.md").write_text(
        "# Test\nOperator GoldenFar\n", encoding="utf-8"
    )
    (repo / "VISION.md").write_text("Vision test", encoding="utf-8")

    data_dir = tmp_path / "data"
    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(
            aria_vector_memory=False,
            aria_public_mode=False,
        ),
    )
    monkeypatch.setenv("ARIA_REPO_ROOT", str(repo))

    msg = f"ingest-repo {repo}"
    text, data = await execute_ingest_repo(msg, lang="fr")

    assert data["ok"] is True
    assert data["files_count"] >= 2
    assert "INGEST-REPO" in text
    assert data["cognitive_added"] >= 2

    report_file = data_dir / "memory" / "ingest_repo_reports.jsonl"
    assert report_file.is_file()
    line = report_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    report = json.loads(line)
    assert report["files_count"] == data["files_count"]
    assert "COLLEGUE.md" in "".join(report["files_read"])


def test_resolve_repo_path_rejects_windows_path_outside_home(tmp_path, monkeypatch):
    """CodeQL py/path-injection: ``message`` is operator-controlled free
    text -- a Windows-style path pointing OUTSIDE the operator's own home
    tree must never be trusted (would let the ingest walk/read an arbitrary
    directory on the machine), even though it's a real, existing directory.
    Relative-path resolution here mirrors the regex's real-world usage: the
    extracted "C:\\..." match is a RELATIVE path once lifted out of a
    Windows-style message, resolved against cwd exactly like the real code
    does -- ``monkeypatch.chdir`` pins that cwd for a deterministic test."""
    fake_home = tmp_path / "home" / "operator"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(ingest_repo_skill.Path, "home", staticmethod(lambda: fake_home))

    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    outside = workdir / "C:\\outside\\secret"
    outside.mkdir(parents=True)

    resolved = _resolve_repo_path("ingest-repo C:\\outside\\secret")

    assert resolved != outside.resolve()


def test_resolve_repo_path_accepts_windows_path_inside_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home" / "operator"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(ingest_repo_skill.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.chdir(fake_home)

    inside = fake_home / "C:\\GitHub-Repos\\ARIA"
    inside.mkdir(parents=True)

    resolved = _resolve_repo_path("ingest-repo C:\\GitHub-Repos\\ARIA")

    assert resolved == inside.resolve()