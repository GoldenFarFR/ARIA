"""Disjoncteur de fournisseur LLM — état persistant + effet réel sur le routage."""
from __future__ import annotations

import pytest

from aria_core import llm_circuit_breaker
from aria_core.paths import configure_data_dir


def test_default_not_armed(tmp_path):
    configure_data_dir(tmp_path)
    assert llm_circuit_breaker.is_armed() is False
    assert llm_circuit_breaker.get_override() is None
    assert llm_circuit_breaker.status() == {"armed": False}


def test_arm_then_disarm(tmp_path):
    configure_data_dir(tmp_path)
    llm_circuit_breaker.arm(
        provider="openrouter",
        model="anthropic/claude-sonnet-5",
        fallback_model="anthropic/claude-haiku-4.5",
        reason="test",
        triggered_by="unit_test",
    )
    assert llm_circuit_breaker.is_armed() is True
    override = llm_circuit_breaker.get_override()
    assert override["provider"] == "openrouter"
    assert override["model"] == "anthropic/claude-sonnet-5"
    assert override["fallback_model"] == "anthropic/claude-haiku-4.5"

    llm_circuit_breaker.disarm(by="operator")
    assert llm_circuit_breaker.is_armed() is False
    assert llm_circuit_breaker.get_override() is None


def test_corrupted_state_fails_open(tmp_path):
    """Un état illisible ne doit jamais faire taire toute la conversation d'ARIA --
    traité comme non armé, jamais une exception propagée."""
    configure_data_dir(tmp_path)
    (tmp_path / "llm_circuit_breaker.json").write_text("{not valid json", encoding="utf-8")
    assert llm_circuit_breaker.is_armed() is False
    assert llm_circuit_breaker.get_override() is None


def test_arm_without_provider_ignored(tmp_path):
    """Un fichier corrompu à la main (provider vide) ne doit jamais faire planter
    le routage -- traité comme non armé."""
    configure_data_dir(tmp_path)
    import json

    (tmp_path / "llm_circuit_breaker.json").write_text(
        json.dumps({"armed": True, "provider": ""}), encoding="utf-8"
    )
    assert llm_circuit_breaker.get_override() is None


class TestResolveRoutesRespectsBreaker:
    """Vérifie l'effet RÉEL sur _resolve_routes (llm.py), pas juste l'état isolé."""

    def test_breaker_off_uses_default_provider(self, tmp_path, monkeypatch):
        configure_data_dir(tmp_path)
        from aria_core import llm
        from aria_core.runtime import get_settings

        monkeypatch.setattr(get_settings(), "llm_provider", "grok")
        monkeypatch.setattr(get_settings(), "grok_api_key", "fake-grok-key")
        routes = llm._resolve_routes()
        assert routes[0].provider == "grok"

    def test_breaker_on_overrides_default_provider(self, tmp_path, monkeypatch):
        configure_data_dir(tmp_path)
        from aria_core import llm, llm_circuit_breaker
        from aria_core.runtime import get_settings

        monkeypatch.setattr(get_settings(), "llm_provider", "grok")
        monkeypatch.setattr(get_settings(), "grok_api_key", "fake-grok-key")
        monkeypatch.setattr(get_settings(), "openrouter_api_key", "fake-openrouter-key")
        llm_circuit_breaker.arm(
            provider="openrouter",
            model="anthropic/claude-sonnet-5",
            fallback_model="anthropic/claude-haiku-4.5",
            reason="test",
        )
        routes = llm._resolve_routes()
        assert routes[0].provider == "openrouter"
        assert routes[0].model == "anthropic/claude-sonnet-5"
        # Le secours désigné par le disjoncteur (Haiku, même provider) doit suivre.
        assert any(r.provider == "openrouter" and r.model == "anthropic/claude-haiku-4.5" for r in routes[1:])

    def test_breaker_on_never_affects_explicit_provider_call(self, tmp_path, monkeypatch):
        """Un appelant qui fixe déjà son propre provider (ex. le tie-breaker momentum
        sur Haiku via OpenRouter) reste inchangé, armé ou non -- il ne dépend jamais
        de Grok, donc le disjoncteur n'a rien à y faire."""
        configure_data_dir(tmp_path)
        from aria_core import llm, llm_circuit_breaker
        from aria_core.runtime import get_settings

        monkeypatch.setattr(get_settings(), "openrouter_api_key", "fake-openrouter-key")
        llm_circuit_breaker.arm(
            provider="openrouter", model="anthropic/claude-sonnet-5", reason="test",
        )
        routes = llm._resolve_routes(provider="openrouter", model="anthropic/claude-haiku-4.5")
        assert routes[0].provider == "openrouter"
        assert routes[0].model == "anthropic/claude-haiku-4.5"
