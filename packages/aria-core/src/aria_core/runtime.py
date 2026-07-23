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
    """Forwarding proxy — never has a `__dict__` of its own, except for the exception below.

    Without explicit `__setattr__`/`__delattr__`, a `setattr(settings, ...)` (e.g.
    `monkeypatch.setattr(settings, "x", v)`, very common in the test suite) would write
    onto the proxy instance itself rather than the real settings object — an attribute that
    would then stay stuck at that value for the whole session, shadowing `__getattr__`
    (i.e. the fresh settings object reconfigured by each test) even after monkeypatch's
    "undo", which restores by reassigning (never by deleting). Forwarding writes just
    like reads eliminates this state leak at the root for real fields.

    Local fallback (`object.__setattr__`) only when the target refuses the write with an
    `AttributeError` — the case of computed read-only properties (e.g. `admin_ids`,
    derived from `telegram_admin_ids`) that many tests deliberately override via
    `monkeypatch.setattr(settings, "admin_ids", ...)`, the only practical way to control a
    pydantic property without a setter. This fallback reproduces the pre-existing behavior
    (so no regression) for this specific subset, never for a real field.
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
