"""Tests préflight mémoire ouvrier + anti-fallback Ollama."""
from __future__ import annotations

import pytest

from ouvrier_runner import _allow_ollama_fallback, run_ouvrier


def test_no_ollama_fallback_when_groq_exhausted(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "groq")
    monkeypatch.delenv("ARIA_OUVRIER_OLLAMA_FALLBACK", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    monkeypatch.setattr("ouvrier_runner.bridge_api_keys", lambda: {})
    monkeypatch.setattr(
        "ouvrier_runner._run_ouvrier_cloud",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("LLM cloud indisponible (429).")),
    )
    monkeypatch.setattr(
        "ouvrier_runner.run_ouvrier_ollama_react",
        lambda _p: pytest.fail("Ollama ne doit pas être appelé"),
    )

    out = run_ouvrier("test quota")
    assert "429" in out
    assert "Ollama" in out or "fallback" in out.lower()


def test_ollama_fallback_explicit_mode(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "ollama")
    assert _allow_ollama_fallback() is True


def test_preflight_memory_disabled(monkeypatch):
    from ouvrier_memory import preflight_memory_context

    monkeypatch.setenv("ARIA_OUVRIER_MEMORY", "off")
    assert preflight_memory_context("qui es-tu") == ""


def test_maybe_record_lesson_cloud_quota():
    from ouvrier_learn import maybe_record_lesson

    maybe_record_lesson(
        "fix acp",
        "Groq est en quota (429) — réessaie dans une minute.",
        channel="test",
    )