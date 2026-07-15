"""Accès dédié aux diagnostics (pool-status, agent-wallet-ledger) — token distinct
du secret admin et du token relay, fail-closed par défaut (cf. CLAUDE.md 15/07)."""
from __future__ import annotations

from aria_core import diagnostics_access as da


def test_disabled_without_token(monkeypatch):
    monkeypatch.delenv("ARIA_DIAGNOSTIC_TOKEN", raising=False)
    assert da.diagnostics_enabled() is False
    assert da.verify_diagnostic_access("anything") is False


def test_enabled_with_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "secretxyz")
    assert da.diagnostics_enabled() is True


def test_verify_rejects_wrong_or_missing_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "secretxyz")
    assert da.verify_diagnostic_access("wrong") is False
    assert da.verify_diagnostic_access(None) is False
    assert da.verify_diagnostic_access("") is False


def test_verify_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "secretxyz")
    assert da.verify_diagnostic_access("secretxyz") is True
    assert da.verify_diagnostic_access(" secretxyz ") is True
