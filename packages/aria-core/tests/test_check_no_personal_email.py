"""Tests du scanner d'email hors liste blanche (incident #139, 12/07)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_no_personal_email as scanner  # noqa: E402


def _write(tmp_path: Path, rel: str, content: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_allowlisted_email_is_not_flagged(tmp_path):
    _write(tmp_path, "packages/aria-core/src/aria_core/identity.py", "EMAIL = 'agentaria.zhc@gmail.com'\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_allowlisted_domain_is_not_flagged(tmp_path):
    _write(tmp_path, "docs/example.md", "contact: someone@example.com\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_unknown_personal_email_is_flagged(tmp_path):
    _write(tmp_path, "packages/aria-core/src/aria_core/truth_ledger/sync.py", "author = 'jean.dupont@gmail.com'\n")
    findings = scanner.find_unallowlisted_emails(tmp_path)
    assert findings == {"packages/aria-core/src/aria_core/truth_ledger/sync.py": ["jean.dupont@gmail.com"]}


def test_test_directory_is_exempt(tmp_path):
    _write(tmp_path, "packages/aria-core/tests/test_vc_delivery.py", "to = 'jean.dupont@gmail.com'\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_test_prefixed_file_is_exempt_even_outside_tests_dir(tmp_path):
    _write(tmp_path, "scripts/test_helper.py", "to = 'jean.dupont@gmail.com'\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_excluded_dirs_are_skipped(tmp_path):
    _write(tmp_path, "node_modules/pkg/index.js", "author: jean.dupont@gmail.com")
    _write(tmp_path, ".venv/lib/site.py", "jean.dupont@gmail.com")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_case_insensitive_allowlist_match(tmp_path):
    _write(tmp_path, "docs/note.md", "Contact: AgentAria.ZHC@GMAIL.COM\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_multiple_findings_across_files(tmp_path):
    _write(tmp_path, "a.py", "x = 'jean.dupont@gmail.com'\n")
    _write(tmp_path, "b.py", "y = 'marie.martin@outlook.com'\n")
    findings = scanner.find_unallowlisted_emails(tmp_path)
    assert set(findings.keys()) == {"a.py", "b.py"}


def test_gmail_domain_not_broadly_allowlisted(tmp_path):
    """Un domaine large (gmail.com) ne doit JAMAIS être allowlisté entièrement -- seule
    l'adresse exacte agentaria.zhc@gmail.com l'est, sinon une vraie adresse personnelle
    sur le même domaine passerait inaperçue (c'est précisément le risque de l'incident)."""
    _write(tmp_path, "x.py", "operateur@gmail.com\n")
    findings = scanner.find_unallowlisted_emails(tmp_path)
    assert findings == {"x.py": ["operateur@gmail.com"]}


def test_main_returns_nonzero_on_findings(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "leak.py", "jean.dupont@gmail.com\n")
    monkeypatch.setattr(scanner, "REPO_ROOT", tmp_path)
    assert scanner.main() == 1
    out = capsys.readouterr().out
    assert "jean.dupont@gmail.com" in out


def test_main_returns_zero_when_clean(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "clean.py", "noreply@anthropic.com\n")
    monkeypatch.setattr(scanner, "REPO_ROOT", tmp_path)
    assert scanner.main() == 0
    out = capsys.readouterr().out
    assert "Aucun email" in out


def test_git_at_github_ssh_syntax_is_allowlisted(tmp_path):
    """Item #232 (30/07), real incident: git@github.com (generic SSH remote
    syntax, not a personal address) re-triggered the 13/07 incident already
    documented in HANDOFF_SECURITE.md -- ironically via the very sentence
    that DESCRIBES that incident."""
    _write(tmp_path, "docs/HANDOFF_SECURITE.md", "cloned via git@github.com:user/repo.git\n")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}


def test_binary_file_with_email_like_byte_noise_is_exempt(tmp_path):
    """Item #232 (30/07), real incident: mobile/assets/icon.png produced a
    false "email" from pure PNG byte noise -- ``read_text(errors="ignore")``
    silently succeeds on binary content instead of raising. Extension-based
    exemption, not a one-off path exception (any future binary asset is
    covered)."""
    p = tmp_path / "mobile" / "assets" / "icon.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    # Invalid UTF-8 bytes surrounding a valid email-shaped ASCII sequence --
    # errors="ignore" would decode this to something like "Pn@a.fF" if the
    # file weren't exempted by extension first.
    p.write_bytes(b"\x89PNG\r\n\x1a\n\xffP\xffn@\xffa\xff.\xfffF\xff\x00\x01")
    assert scanner.find_unallowlisted_emails(tmp_path) == {}
