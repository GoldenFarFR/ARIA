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
    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)


settings = _SettingsProxy()
