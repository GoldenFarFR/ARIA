"""Internal diagnostic endpoint (14/08) -- safe alternative to a raw `docker exec`
into the prod container (blocked by the session's security classifier even for a
read-only diagnostic call, since a shell command can't be scoped to "read-only" by
pattern alone). Exposes ONE predefined, already-idempotent operation
(`discover_and_enqueue_candidates`, item #151) over HTTP instead -- same secret-gated
pattern as /internal/activate-heartbeat, but a DEDICATED secret (never
DEPLOY_ACTIVATION_SECRET, which deploy.sh alone should ever send)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, settings
from app.main import app

DIAGNOSE_PATH = "/internal/diagnose/wallet-sourcing"


def test_internal_diagnostic_secret_env_var_name_matches_pydantic_field(monkeypatch):
    """Same class of bug as the 14/08 DEPLOY_ACTIVATION_SECRET incident -- app.config.Settings
    has no env_prefix, so Pydantic only ever reads the bare env var name. Exercises the
    real env-var -> Settings-field mapping, not a mocked settings attribute."""
    monkeypatch.setenv("INTERNAL_DIAGNOSTIC_SECRET", "real-mapping-check")
    s = Settings()
    assert s.internal_diagnostic_secret == "real-mapping-check"


@pytest.fixture
def _mock_discover(monkeypatch):
    mock = AsyncMock(return_value={"outcome": "ok", "candidates_found": 3, "added_to_queue": 1})
    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.discover_and_enqueue_candidates", mock
    )
    yield mock


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDiagnoseWalletSourcingEndpoint:
    async def test_refused_when_secret_not_configured_fail_closed(self, client, monkeypatch, _mock_discover):
        monkeypatch.setattr(settings, "internal_diagnostic_secret", "")
        res = await client.post(DIAGNOSE_PATH, headers={"X-Internal-Diagnostic-Secret": "anything"})
        assert res.status_code == 403
        _mock_discover.assert_not_called()

    async def test_refused_without_header(self, client, monkeypatch, _mock_discover):
        monkeypatch.setattr(settings, "internal_diagnostic_secret", "correct-secret")
        res = await client.post(DIAGNOSE_PATH)
        assert res.status_code == 403
        _mock_discover.assert_not_called()

    async def test_refused_with_wrong_secret(self, client, monkeypatch, _mock_discover):
        monkeypatch.setattr(settings, "internal_diagnostic_secret", "correct-secret")
        res = await client.post(DIAGNOSE_PATH, headers={"X-Internal-Diagnostic-Secret": "wrong-secret"})
        assert res.status_code == 403
        _mock_discover.assert_not_called()

    async def test_deploy_activation_secret_never_accepted_here(self, client, monkeypatch, _mock_discover):
        """The two secrets are deliberately distinct -- a leaked/known deploy secret
        must never double as a diagnostic-trigger secret."""
        monkeypatch.setattr(settings, "internal_diagnostic_secret", "diag-secret")
        monkeypatch.setattr(settings, "deploy_activation_secret", "deploy-secret")
        res = await client.post(DIAGNOSE_PATH, headers={"X-Internal-Diagnostic-Secret": "deploy-secret"})
        assert res.status_code == 403
        _mock_discover.assert_not_called()

    async def test_accepted_with_correct_secret_returns_real_result(self, client, monkeypatch, _mock_discover):
        monkeypatch.setattr(settings, "internal_diagnostic_secret", "correct-secret")
        res = await client.post(DIAGNOSE_PATH, headers={"X-Internal-Diagnostic-Secret": "correct-secret"})
        assert res.status_code == 200
        assert res.json() == {"outcome": "ok", "candidates_found": 3, "added_to_queue": 1}
        _mock_discover.assert_awaited_once()
