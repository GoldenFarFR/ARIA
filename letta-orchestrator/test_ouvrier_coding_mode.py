"""Tests mode débranchement Grok."""
from __future__ import annotations

from ouvrier_coding_mode import (
    is_coding_task,
    should_debranch,
    strip_coding_triggers,
    wants_coding_pure,
)
from ouvrier_runner import _build_cloud_system, _cloud_candidates


def test_wants_coding_pure_commands():
    assert wants_coding_pure("/grok-coding implémente le fix")
    assert wants_coding_pure("!débranche et corrige pytest")
    assert wants_coding_pure("mode grok coding sur aria-core")
    assert not wants_coding_pure("quelle est la météo")


def test_is_coding_task():
    assert is_coding_task("corrige le test_truth_ledger")
    assert is_coding_task("git commit et push")
    assert not is_coding_task("raconte une blague")


def test_should_debranch_skips_brain_on_code():
    skip, pure = should_debranch("fix le module acp_cli")
    assert skip is True
    assert pure is True


def test_should_debranch_bootstrap_without_code():
    skip, pure = should_debranch("bonjour", needs_bootstrap=True)
    assert skip is True
    assert pure is False


def test_strip_coding_triggers():
    assert strip_coding_triggers("/grok-coding fix pytest") == "fix pytest"


def test_cloud_candidates_coding_pure_grok_only(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    chain = _cloud_candidates(coding_pure=True)
    assert len(chain) == 1
    assert chain[0][0] == "grok"


def test_build_cloud_system_coding_pure():
    text = _build_cloud_system(coding_pure=True)
    assert "débranchement" in text.lower()
    assert "outils repo" in text.lower()