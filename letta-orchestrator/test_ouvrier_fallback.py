"""Tests chaîne fallback ouvrier grok → groq → ollama."""
from __future__ import annotations

import pytest

from ouvrier_runner import _cloud_candidates, _ouvrier_cloud_mode, run_ouvrier


def test_cloud_chain_grok_then_groq(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "auto")
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    chain = _cloud_candidates()
    assert len(chain) == 2
    assert chain[0][0] == "grok"
    assert chain[1][0] == "groq"


def test_cloud_chain_groq_only(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    monkeypatch.setattr("ouvrier_runner.bridge_api_keys", lambda: {})
    chain = _cloud_candidates()
    assert len(chain) == 1
    assert chain[0][0] == "groq"


def test_cloud_mode_groq_skips_grok(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "groq")
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    monkeypatch.setattr("ouvrier_runner.bridge_api_keys", lambda: {})
    assert _ouvrier_cloud_mode() == "groq"
    chain = _cloud_candidates()
    assert len(chain) == 1
    assert chain[0][0] == "groq"


def test_cloud_mode_ollama_no_cloud(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "ollama")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    assert _ouvrier_cloud_mode() == "ollama"
    assert _cloud_candidates() == []


def test_run_ouvrier_falls_back_grok_to_groq(monkeypatch):
    calls: list[str] = []

    def fake_cloud(user_prompt, provider, url, api_key, model, **kwargs):
        calls.append(provider)
        if provider == "grok":
            raise RuntimeError("LLM cloud indisponible (403).")
        return "Réponse Groq OK"

    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "auto")
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    monkeypatch.setattr("ouvrier_runner._run_ouvrier_cloud", fake_cloud)
    monkeypatch.delenv("ARIA_OUVRIER_OLLAMA_FALLBACK", raising=False)
    monkeypatch.setattr(
        "ouvrier_runner.run_ouvrier_ollama_react",
        lambda _p: (_ for _ in ()).throw(AssertionError("no ollama")),
    )

    out = run_ouvrier("test fallback")
    assert out == "Réponse Groq OK"
    assert calls == ["grok", "groq"]


def test_run_ouvrier_no_ollama_after_groq_fail(monkeypatch):
    monkeypatch.setenv("ARIA_OUVRIER_CLOUD", "groq")
    monkeypatch.delenv("ARIA_OUVRIER_OLLAMA_FALLBACK", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._vault_key", lambda *_a, **_k: "")
    monkeypatch.setattr(
        "ouvrier_runner._run_ouvrier_cloud",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("LLM cloud indisponible (429).")),
    )
    monkeypatch.setattr(
        "ouvrier_runner.run_ouvrier_ollama_react",
        lambda _p: (_ for _ in ()).throw(AssertionError("no ollama")),
    )
    out = run_ouvrier("quota test")
    assert "429" in out