"""#154 — logique de bascule blue-green de deploy.sh (vanguard/deploy_lib.sh) et
disjoncteur autoheal (vanguard/scripts/autoheal_lib.sh). Fonctions bash PURES (aucun
docker/nginx réel) -- testées ici en shellant `bash -c "source <lib>; <fn> <args>"`
pour ne pas dupliquer la logique en Python."""
from pathlib import Path
import subprocess

import pytest

VANGUARD_DIR = Path(__file__).resolve().parents[2]
DEPLOY_LIB = VANGUARD_DIR / "deploy_lib.sh"
AUTOHEAL_LIB = VANGUARD_DIR / "scripts" / "autoheal_lib.sh"


def _run(lib: Path, call: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source '{lib}'; {call}"],
        capture_output=True,
        text=True,
    )


class TestReadActivePort:
    def test_reads_port_from_upstream_conf(self, tmp_path):
        f = tmp_path / "upstream.conf"
        f.write_text("upstream aria_api_backend {\n    server 127.0.0.1:8000;\n}\n")
        res = _run(DEPLOY_LIB, f"read_active_port '{f}'")
        assert res.returncode == 0
        assert res.stdout.strip() == "8000"

    def test_reads_alternate_port(self, tmp_path):
        f = tmp_path / "upstream.conf"
        f.write_text("upstream aria_api_backend {\n    server 127.0.0.1:8001;\n}\n")
        res = _run(DEPLOY_LIB, f"read_active_port '{f}'")
        assert res.stdout.strip() == "8001"

    def test_missing_file_fails_explicitly_no_guessing(self, tmp_path):
        f = tmp_path / "missing.conf"
        res = _run(DEPLOY_LIB, f"read_active_port '{f}'")
        assert res.returncode != 0
        assert "introuvable" in res.stderr

    def test_malformed_file_fails_explicitly(self, tmp_path):
        f = tmp_path / "upstream.conf"
        f.write_text("not a valid upstream block\n")
        res = _run(DEPLOY_LIB, f"read_active_port '{f}'")
        assert res.returncode != 0


class TestStandbyPort:
    def test_8000_flips_to_8001(self):
        res = _run(DEPLOY_LIB, "standby_port 8000")
        assert res.stdout.strip() == "8001"

    def test_8001_flips_to_8000(self):
        res = _run(DEPLOY_LIB, "standby_port 8001")
        assert res.stdout.strip() == "8000"

    def test_unexpected_port_fails_never_guesses(self):
        res = _run(DEPLOY_LIB, "standby_port 9999")
        assert res.returncode != 0
        assert "inattendu" in res.stderr


class TestRenderUpstreamConf:
    def test_renders_expected_block(self):
        res = _run(DEPLOY_LIB, "render_upstream_conf 8001")
        assert res.returncode == 0
        assert res.stdout == "upstream aria_api_backend {\n    server 127.0.0.1:8001;\n}\n"

    def test_roundtrips_through_read_active_port(self, tmp_path):
        render = _run(DEPLOY_LIB, "render_upstream_conf 8000")
        f = tmp_path / "upstream.conf"
        f.write_text(render.stdout)
        res = _run(DEPLOY_LIB, f"read_active_port '{f}'")
        assert res.stdout.strip() == "8000"


class TestAutohealCircuitBreaker:
    def test_counts_up_within_window(self, tmp_path):
        state = tmp_path / "state"
        base = 1_700_000_000
        for i, ts in enumerate([base, base + 100, base + 200], start=1):
            res = _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {ts}")
            assert res.stdout.strip() == str(i)

    def test_prunes_entries_outside_the_sliding_window(self, tmp_path):
        state = tmp_path / "state"
        base = 1_700_000_000
        _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {base}")
        _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {base + 100}")
        # cutoff = (base+700) - 600 = base+100 : la 1ère entrée (base) sort de la
        # fenêtre, la 2e (base+100) y reste tout juste (>= cutoff).
        res = _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {base + 700}")
        assert res.stdout.strip() == "2"

    def test_reaches_cap_after_three_transitions_in_window(self, tmp_path):
        state = tmp_path / "state"
        base = 1_700_000_000
        counts = []
        for ts in [base, base + 60, base + 120]:
            res = _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {ts}")
            counts.append(int(res.stdout.strip()))
        assert counts == [1, 2, 3]

    def test_empty_state_file_after_full_prune_counts_zero(self, tmp_path):
        state = tmp_path / "state"
        base = 1_700_000_000
        _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {base}")
        res = _run(AUTOHEAL_LIB, f"record_and_count '{state}' 600 {base + 10_000}")
        assert res.stdout.strip() == "1"


@pytest.mark.parametrize("lib", [DEPLOY_LIB, AUTOHEAL_LIB])
def test_lib_files_exist(lib):
    assert lib.is_file(), f"{lib} manquant"
