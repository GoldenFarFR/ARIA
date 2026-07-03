"""Tests chaîne fallback ouvrier grok → groq → ollama."""
from __future__ import annotations

import pytest

from ouvrier_runner import _cloud_candidates, run_ouvrier


def test_cloud_chain_grok_then_groq(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
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


def test_run_ouvrier_falls_back_grok_to_groq(monkeypatch):
    calls: list[str] = []

    def fake_cloud(user_prompt, provider, url, api_key, model):
        calls.append(provider)
        if provider == "grok":
            raise RuntimeError("LLM cloud indisponible (403).")
        return "Réponse Groq OK"

    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a" * 40)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "b" * 48)
    monkeypatch.setattr("ouvrier_runner._run_ouvrier_cloud", fake_cloud)
    monkeypatch.setattr("ouvrier_runner.run_ouvrier_ollama_react", lambda _p: "Ollama")

    out = run_ouvrier("test fallback")
    assert out == "Réponse Groq OK"
    assert calls == ["grok", "groq"]