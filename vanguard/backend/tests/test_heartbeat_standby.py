"""Blue-green standby window (14/08) -- root cause of the paper-trading persistence
bug (SAPIEN/WMTX vanished, trading_mode stuck 8.5 days, watchdog-log.md 11/08-14/08):
deploy.sh's standby container boots with the SAME SQLite bind-mount as the active
one, and aria_heartbeat.start() used to fire unconditionally at FastAPI startup --
two heartbeat loops writing to the same DB during the ~40s health-check + cutover
window, no lock, no coordination (see docs/HANDOFF_VPS_OPS.md).

Fix: the standby container boots with ARIA_HEARTBEAT_STANDBY set and skips
aria_heartbeat.start() entirely; deploy.sh calls POST /internal/activate-heartbeat
(secret-gated, same is_operator_request doctrine as every other admin route) only
after the real traffic cutover through nginx is confirmed."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from aria_core.heartbeat import aria_heartbeat
from app.config import Settings, settings
from app.main import _heartbeat_standby_enabled, app

ACTIVATE_PATH = "/internal/activate-heartbeat"


def test_deploy_activation_secret_env_var_name_matches_pydantic_field(monkeypatch):
    """Real incident (14/08): deploy.sh sent X-Deploy-Activation-Secret using a value
    read from ARIA_DEPLOY_ACTIVATION_SECRET in .env -- but app.config.Settings has no
    env_prefix configured (unlike a naive assumption), so Pydantic only ever looks for
    DEPLOY_ACTIVATION_SECRET (same convention as admin_api_secret -> ADMIN_API_SECRET).
    The endpoint's mocked-settings tests below never caught this because they
    monkeypatch settings.deploy_activation_secret directly, bypassing the env-var name
    entirely -- exactly the gap that let this ship. This test exercises the REAL
    env-var-name -> Settings-field mapping, the one thing the mocked tests couldn't."""
    monkeypatch.setenv("DEPLOY_ACTIVATION_SECRET", "real-mapping-check")
    s = Settings()
    assert s.deploy_activation_secret == "real-mapping-check"


class TestHeartbeatStandbyEnabled:
    """Pure decision function -- no FastAPI/DB involved, exhaustively testable."""

    @pytest.mark.parametrize("raw", [None, "", "0", "false", "False", "no"])
    def test_disabled_by_default_and_on_falsy_values(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv("ARIA_HEARTBEAT_STANDBY", raising=False)
        else:
            monkeypatch.setenv("ARIA_HEARTBEAT_STANDBY", raw)
        assert _heartbeat_standby_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE"])
    def test_enabled_on_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("ARIA_HEARTBEAT_STANDBY", raw)
        assert _heartbeat_standby_enabled() is True


@pytest.fixture(autouse=True)
def _mock_heartbeat(monkeypatch):
    """Never let a test actually start the real heartbeat loop (network/DB side
    effects) -- only assert whether the endpoint attempted to."""
    mock_start = AsyncMock()
    monkeypatch.setattr(aria_heartbeat, "start", mock_start)
    monkeypatch.setattr(aria_heartbeat, "_running", False)
    yield mock_start


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestActivateHeartbeatEndpoint:
    async def test_refused_when_secret_not_configured_fail_closed(self, client, monkeypatch, _mock_heartbeat):
        monkeypatch.setattr(settings, "deploy_activation_secret", "")
        res = await client.post(ACTIVATE_PATH, headers={"X-Deploy-Activation-Secret": "anything"})
        assert res.status_code == 403
        _mock_heartbeat.assert_not_called()

    async def test_refused_without_header(self, client, monkeypatch, _mock_heartbeat):
        monkeypatch.setattr(settings, "deploy_activation_secret", "correct-secret")
        res = await client.post(ACTIVATE_PATH)
        assert res.status_code == 403
        _mock_heartbeat.assert_not_called()

    async def test_refused_with_wrong_secret(self, client, monkeypatch, _mock_heartbeat):
        monkeypatch.setattr(settings, "deploy_activation_secret", "correct-secret")
        res = await client.post(ACTIVATE_PATH, headers={"X-Deploy-Activation-Secret": "wrong-secret"})
        assert res.status_code == 403
        _mock_heartbeat.assert_not_called()

    async def test_accepted_with_correct_secret_starts_heartbeat(self, client, monkeypatch, _mock_heartbeat):
        monkeypatch.setattr(settings, "deploy_activation_secret", "correct-secret")
        res = await client.post(ACTIVATE_PATH, headers={"X-Deploy-Activation-Secret": "correct-secret"})
        assert res.status_code == 200
        assert res.json()["outcome"] == "activated"
        _mock_heartbeat.assert_awaited_once()

    async def test_idempotent_second_call_reports_already_active(self, client, monkeypatch, _mock_heartbeat):
        monkeypatch.setattr(settings, "deploy_activation_secret", "correct-secret")
        headers = {"X-Deploy-Activation-Secret": "correct-secret"}
        first = await client.post(ACTIVATE_PATH, headers=headers)
        assert first.json()["outcome"] == "activated"

        monkeypatch.setattr(aria_heartbeat, "_running", True)
        second = await client.post(ACTIVATE_PATH, headers=headers)
        assert second.status_code == 200
        assert second.json()["outcome"] == "already_active"
        # start() is idempotent internally (AriaHeartbeat.start already no-ops when
        # _running) -- the endpoint still calls it unconditionally, safety net.
        assert _mock_heartbeat.await_count == 2
