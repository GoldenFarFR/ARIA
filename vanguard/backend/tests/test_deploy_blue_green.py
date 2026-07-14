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


class TestRetryUntil:
    """#154 (correctif post-déploiement) : la vérification finale de deploy.sh tirait
    un unique curl juste après `systemctl reload nginx`, sans marge -- reload pas
    instantané, faux échec possible. retry_until (identique à deploy_vitrine_lib.sh,
    #157) retente sur un plafond court plutôt qu'un essai unique."""

    def test_succeeds_immediately(self):
        res = _run(DEPLOY_LIB, "retry_until 3 0.05 true")
        assert res.returncode == 0

    def test_succeeds_within_cap_after_failures(self, tmp_path):
        state = tmp_path / "counter"
        flaky = (
            "flaky() { local n=0; [ -f \"$1\" ] && n=$(cat \"$1\"); "
            "n=$((n+1)); echo \"$n\" > \"$1\"; [ \"$n\" -ge 3 ]; }"
        )
        res = _run(DEPLOY_LIB, f"{flaky}; retry_until 5 0.05 flaky '{state}'")
        assert res.returncode == 0, res.stderr
        assert state.read_text().strip() == "3"

    def test_fails_after_exhausting_cap(self):
        res = _run(DEPLOY_LIB, "retry_until 3 0.05 false")
        assert res.returncode != 0

    def test_identical_to_deploy_vitrine_lib_implementation(self):
        """#157 (deploy_vitrine_lib.sh) définit la MÊME fonction -- vérifie qu'elles ne
        divergent pas silencieusement entre les deux scripts (même besoin, même code)."""
        vitrine_lib = VANGUARD_DIR / "deploy_vitrine_lib.sh"
        deploy_src = DEPLOY_LIB.read_text()
        vitrine_src = vitrine_lib.read_text()

        def _extract_body(src: str) -> str:
            start = src.index("retry_until() {")
            end = src.index("\n}\n", start) + len("\n}\n")
            return src[start:end]

        assert _extract_body(deploy_src) == _extract_body(vitrine_src)


@pytest.mark.parametrize("lib", [DEPLOY_LIB, AUTOHEAL_LIB])
def test_lib_files_exist(lib):
    assert lib.is_file(), f"{lib} manquant"
