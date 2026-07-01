"""Bootstrap aria-core before test modules import truth_ledger (DB_PATH at import)."""
from __future__ import annotations

import pytest

from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

configure_test_runtime(settings=AriaRuntimeSettings())


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path):
    """Fresh settings + writable auth DB for each test."""
    auth_db = tmp_path / "auth.db"
    configure_test_runtime(auth_db_path=auth_db, settings=AriaRuntimeSettings())
    yield


@pytest.fixture
def test_settings() -> AriaRuntimeSettings:
    from aria_core.runtime import get_settings

    return get_settings()