"""Runtime settings bridge — configured by host (dexpulse) at boot."""
from __future__ import annotations

from typing import Any

_settings: Any = None


def configure(settings: Any) -> None:
    global _settings
    _settings = settings


def get_settings() -> Any:
    if _settings is None:
        raise RuntimeError("aria_core not configured — call bootstrap.configure() at host startup")
    return _settings


class _SettingsProxy:
    """Forwarding proxy — jamais de `__dict__` propre à elle, sauf exception ci-dessous.

    Sans `__setattr__`/`__delattr__` explicites, un `setattr(settings, ...)` (ex.
    `monkeypatch.setattr(settings, "x", v)`, très répandu dans la suite de tests) écrirait
    sur l'instance proxy elle-même plutôt que sur l'objet settings réel — un attribut qui
    resterait alors bloqué sur cette valeur pour toute la session, masquant `__getattr__`
    (donc l'objet settings frais reconfiguré par chaque test) même après le "undo" de
    monkeypatch, qui restaure en réaffectant (jamais en supprimant). Forwarder l'écriture
    comme la lecture élimine cette fuite d'état à la racine pour les vrais champs.

    Repli local (`object.__setattr__`) uniquement quand la cible refuse l'écriture par
    `AttributeError` — cas des propriétés calculées en lecture seule (ex. `admin_ids`,
    dérivée de `telegram_admin_ids`) que de nombreux tests sur-écrivent volontairement via
    `monkeypatch.setattr(settings, "admin_ids", ...)`, seul moyen pratique de contrôler une
    property pydantic sans setter. Ce repli reproduit le comportement déjà existant (donc
    aucune régression) pour ce sous-ensemble précis, jamais pour un champ réel.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        try:
            setattr(get_settings(), name, value)
        except AttributeError:
            object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        try:
            delattr(get_settings(), name)
        except AttributeError:
            object.__delattr__(self, name)


settings = _SettingsProxy()
