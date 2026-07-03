"""Bootstrap aria-core before test modules import truth_ledger (DB_PATH at import)."""
from __future__ import annotations

import pytest

from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

configure_test_runtime(settings=AriaRuntimeSettings())

# Variables opérateur local (vault/sync) — ne doivent pas fausser les tests unitaires.
_ISOLATED_ENV_KEYS = (
    "ARIA_VECTOR_MEMORY",
    "ARIA_AVATAR_STYLE_INTERVAL_DAYS",
    "ARIA_AVATAR_STYLE_ENABLED",
    "ARIA_VISUAL_AUTONOMY",
    "ARIA_VISUAL_AUTO_APPLY",
    "GITHUB_PROTECTED_REPOS",
)


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """Fresh settings + writable auth DB for each test."""
    for key in _ISOLATED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    auth_db = tmp_path / "auth.db"
    configure_test_runtime(auth_db_path=auth_db, settings=AriaRuntimeSettings())
    yield


@pytest.fixture
def test_settings() -> AriaRuntimeSettings:
    from aria_core.runtime import get_settings

    return get_settings()