import json

import pytest

from aria_core.skills.ingest_repo_skill import (
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
        "# Test\nSylvain GoldenFar\n", encoding="utf-8"
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