"""Flags mémoire vectorielle — évite imports circulaires."""
from __future__ import annotations


def is_vector_enabled() -> bool:
    try:
        from aria_core.runtime import settings

        return bool(getattr(settings, "aria_vector_memory", False))
    except RuntimeError:
        return False