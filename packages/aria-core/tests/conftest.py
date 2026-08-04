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
    """Fresh settings + writable auth DB + isolated data_dir for each test.

    #149 (13/07) : sans ``data_dir`` explicite ici, ``configure_test_runtime()`` retombait
    sur ``Path.cwd() / ".aria-test-data"`` -- un répertoire PARTAGÉ et PERSISTANT entre
    tests et entre lancements successifs de la suite (gitignored, jamais nettoyé). Le
    SQLite qu'il contient (aria.db) accumulait de vraies écritures (ex. release_pipeline
    faisait passer chaque munition du manifeste à "live") jusqu'à épuiser tout le
    manifeste -- `test_release_publisher_matches_injectable_signature` échouait alors de
    façon non-déterministe, dépendante de l'historique des lancements précédents, pas du
    code testé. Chaque test reçoit désormais son propre ``tmp_path`` isolé, comme c'était
    déjà le cas pour ``auth_db_path``.
    """
    for key in _ISOLATED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    auth_db = tmp_path / "auth.db"
    configure_test_runtime(
        data_dir=tmp_path / "data", auth_db_path=auth_db, settings=AriaRuntimeSettings()
    )
    yield


@pytest.fixture
def test_settings() -> AriaRuntimeSettings:
    from aria_core.runtime import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
async def _drain_circuit_breaker_background_tasks():
    """04/08 -- ``circuit_breaker_log.record_transition_nowait`` schedules a
    fire-and-forget ``asyncio.Task`` (best-effort DB write) onto the CURRENT
    event loop. Any test that drives a service module past its failure
    threshold (blockscout/dexscreener/goplus/wallet_transfers_fast/momentum_
    entry's OHLCV cascade -- exercised by plenty of PRE-EXISTING error/retry
    tests, not just the new circuit-breaker-log tests) now schedules one of
    these. pytest-asyncio tears down the loop at the end of each async test;
    without draining here first, an in-flight task's aiosqlite worker thread
    tries to signal a closed loop (`RuntimeError: Event loop is closed`,
    surfaced as a noisy `PytestUnhandledThreadExceptionWarning`). Draining
    after every test (not just circuit-breaker tests) keeps the whole suite
    quiet regardless of which test happens to cross a threshold."""
    yield
    from aria_core import circuit_breaker_log

    if circuit_breaker_log._background_tasks:
        import asyncio

        await asyncio.gather(*list(circuit_breaker_log._background_tasks), return_exceptions=True)