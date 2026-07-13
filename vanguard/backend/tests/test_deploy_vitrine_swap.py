"""#157 — logique de bascule/restauration/retry de deploy-vitrine.sh
(vanguard/deploy_vitrine_lib.sh). Fonctions bash PURES (uniquement mv/rm sur le
système de fichiers local + une boucle de retry générique, aucun docker/nginx/réseau
réel) -- testées ici en shellant `bash -c "source <lib>; <fn> <args>"`."""
from pathlib import Path
import subprocess

import pytest

VANGUARD_DIR = Path(__file__).resolve().parents[2]
VITRINE_LIB = VANGUARD_DIR / "deploy_vitrine_lib.sh"


def _run(call: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source '{VITRINE_LIB}'; {call}"],
        capture_output=True,
        text=True,
    )


def _write(path: Path, content: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text(content)


class TestPublishAtomic:
    def test_first_deploy_no_previous_webroot(self, tmp_path):
        webroot = tmp_path / "webroot"
        tmp_dir = tmp_path / "tmp_new"
        _write(tmp_dir, "new content")

        res = _run(f"publish_atomic '{webroot}' '{tmp_dir}'")
        assert res.returncode == 0, res.stderr
        assert (webroot / "index.html").read_text() == "new content"
        assert not (tmp_path / "webroot.old").exists()

    def test_moves_previous_content_to_old(self, tmp_path):
        webroot = tmp_path / "webroot"
        _write(webroot, "old content")
        tmp_dir = tmp_path / "tmp_new"
        _write(tmp_dir, "new content")

        res = _run(f"publish_atomic '{webroot}' '{tmp_dir}'")
        assert res.returncode == 0, res.stderr
        assert (webroot / "index.html").read_text() == "new content"
        old = Path(f"{webroot}.old")
        assert old.is_dir()
        assert (old / "index.html").read_text() == "old content"


class TestCleanupOld:
    def test_removes_old_keeps_webroot(self, tmp_path):
        webroot = tmp_path / "webroot"
        _write(webroot, "current")
        old = Path(f"{webroot}.old")
        _write(old, "stale")

        res = _run(f"cleanup_old '{webroot}'")
        assert res.returncode == 0, res.stderr
        assert not old.exists()
        assert (webroot / "index.html").read_text() == "current"


class TestRestoreFromOld:
    def test_swaps_back_and_preserves_broken_content(self, tmp_path):
        webroot = tmp_path / "webroot"
        _write(webroot, "broken new content")
        old = Path(f"{webroot}.old")
        _write(old, "previous good content")

        res = _run(f"restore_from_old '{webroot}'")
        assert res.returncode == 0, res.stderr
        assert (webroot / "index.html").read_text() == "previous good content"
        failed = Path(f"{webroot}.failed")
        assert failed.is_dir()
        assert (failed / "index.html").read_text() == "broken new content"
        assert not old.exists()

    def test_fails_explicitly_without_old(self, tmp_path):
        webroot = tmp_path / "webroot"
        _write(webroot, "only content, no .old")

        res = _run(f"restore_from_old '{webroot}'")
        assert res.returncode != 0
        assert "impossible" in res.stderr
        # rien n'a bougé
        assert (webroot / "index.html").read_text() == "only content, no .old"

    def test_overwrites_previous_failed_no_accumulation(self, tmp_path):
        webroot = tmp_path / "webroot"
        old = Path(f"{webroot}.old")
        failed = Path(f"{webroot}.failed")

        _write(webroot, "first broken attempt")
        _write(old, "good baseline")
        _run(f"restore_from_old '{webroot}'")
        assert (failed / "index.html").read_text() == "first broken attempt"

        # deuxième échec : il faut un nouveau .old pour retenter une restauration
        _write(webroot, "second broken attempt")
        _write(old, "good baseline")
        _run(f"restore_from_old '{webroot}'")
        assert (failed / "index.html").read_text() == "second broken attempt"


class TestRetryUntil:
    def test_succeeds_immediately(self, tmp_path):
        res = _run("retry_until 3 0.05 true")
        assert res.returncode == 0

    def test_succeeds_within_cap_after_failures(self, tmp_path):
        state = tmp_path / "counter"
        flaky = (
            "flaky() { local n=0; [ -f \"$1\" ] && n=$(cat \"$1\"); "
            "n=$((n+1)); echo \"$n\" > \"$1\"; [ \"$n\" -ge 3 ]; }"
        )
        res = _run(f"{flaky}; retry_until 5 0.05 flaky '{state}'")
        assert res.returncode == 0, res.stderr
        assert state.read_text().strip() == "3"

    def test_fails_after_exhausting_cap(self, tmp_path):
        res = _run("retry_until 3 0.05 false")
        assert res.returncode != 0


@pytest.mark.parametrize("fn", ["publish_atomic", "cleanup_old", "restore_from_old", "retry_until"])
def test_all_expected_functions_defined(fn):
    res = _run(f"declare -f {fn} >/dev/null")
    assert res.returncode == 0, f"{fn} non défini dans {VITRINE_LIB}"


def test_lib_file_exists():
    assert VITRINE_LIB.is_file(), f"{VITRINE_LIB} manquant"
