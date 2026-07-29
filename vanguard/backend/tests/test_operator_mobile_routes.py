"""Operator mobile routes (Item #201) -- ASGI in-process, real TOTP codes (no
mocked crypto), same doctrine as test_security_hardening.py: offline, no secrets,
no network. Covers the plan's required route coverage: login refused without
TOTP, logout idempotent, /aria/ops/* exempted from the global Privy gate but
individually protected, /status reflects real state."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from aria_core.admin_totp import generate_secret, totp_code
from app.api.routes import operator_mobile
from app.auth import operator_account as accounts
from app.auth import operator_auth_log as auth_log
from app.auth import operator_session as sessions
from app.config import settings
from app.database import init_db
from app.main import app

USERNAME = "operator"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(sessions, "DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(auth_log, "DB_PATH", str(tmp_path / "auth_log.db"))
    yield


@pytest.fixture(autouse=True)
def _no_login_sleep(monkeypatch):
    # The progressive slowdown is real logic worth keeping (tested directly in
    # test_operator_account.py) -- but sleeping for real would make this whole
    # file slow for no added coverage here.
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(operator_mobile.asyncio, "sleep", _instant)


@pytest.fixture
async def totp_secret():
    return generate_secret()


@pytest.fixture(autouse=True)
async def _account(totp_secret, _isolated_db):
    # Explicit dependency on _isolated_db: autouse fixtures without one are not
    # guaranteed to run in declaration order, and this one must write to the
    # patched DB_PATH, never the real default path.
    await accounts.create_or_replace_account(
        username=USERNAME, password=PASSWORD, totp_secret=totp_secret,
    )
    yield


@pytest.fixture
async def client(tmp_path, monkeypatch):
    dexpulse_db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.database.DB_PATH", str(dexpulse_db))
    monkeypatch.setattr(settings, "access_code_enabled", True)
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _login_body(totp_secret, *, password=PASSWORD, code=None):
    return {
        "username": USERNAME,
        "password": password,
        "totp_code": code if code is not None else totp_code(totp_secret),
        "installation_id": "test-device",
    }


# ── /login ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_succeeds_with_correct_password_and_totp(client, totp_secret):
    res = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    assert res.status_code == 200
    token = res.json()["token"]
    assert "." in token


@pytest.mark.asyncio
async def test_login_rejected_without_valid_totp(client, totp_secret):
    res = await client.post("/api/aria/ops/login", json=_login_body(totp_secret, code="000000"))
    assert res.status_code == 401
    events = await auth_log.list_events()
    assert events[0]["event_type"] == "login_failure"


@pytest.mark.asyncio
async def test_login_rejected_with_wrong_password(client, totp_secret):
    res = await client.post("/api/aria/ops/login", json=_login_body(totp_secret, password="wrong"))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_username_same_failure_shape(client, totp_secret):
    body = _login_body(totp_secret)
    body["username"] = "nobody"
    res = await client.post("/api/aria/ops/login", json=body)
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid credentials"


# ── /aria/ops/* exemption + per-route protection ────────────────────────────

@pytest.mark.asyncio
async def test_status_without_auth_rejected_by_route_not_by_privy_gate(client):
    """Confirms the middleware exemption: a 403 from the route itself (invalid
    session), never the generic 401 'Member session required' from AccessCodeMiddleware."""
    res = await client.get("/api/aria/ops/status")
    assert res.status_code == 403
    assert "Member session required" not in res.text


@pytest.mark.asyncio
async def test_status_with_legacy_admin_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "test-legacy-secret")
    res = await client.get("/api/aria/ops/status", headers={"X-Admin-Secret": "test-legacy-secret"})
    assert res.status_code == 200
    assert res.json()["paused"] is False


@pytest.mark.asyncio
async def test_status_with_mobile_session_reflects_real_pause_state(client, totp_secret, monkeypatch):
    from aria_core import outgoing_pause

    monkeypatch.setattr(outgoing_pause, "_read_raw", lambda: {"paused": True, "by": "test", "reason": "drill"})

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]

    res = await client.get("/api/aria/ops/status", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["paused"] is True
    assert body["by"] == "test"


# ── /session ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_returns_identity_and_version(client, totp_secret):
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]

    res = await client.get("/api/aria/ops/session", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == USERNAME
    assert body["role"] == "owner"
    assert body["mobile_api"] == operator_mobile.MOBILE_API_VERSION


@pytest.mark.asyncio
async def test_session_rejects_legacy_admin_secret_alone(client, monkeypatch):
    """/session needs an actual mobile account identity -- the legacy secret path
    has no account_id, so it must NOT satisfy this route."""
    monkeypatch.setattr(settings, "admin_api_secret", "test-legacy-secret")
    res = await client.get("/api/aria/ops/session", headers={"X-Admin-Secret": "test-legacy-secret"})
    assert res.status_code == 403


# ── /logout ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_revokes_real_session(client, totp_secret):
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]

    res = await client.post("/api/aria/ops/logout", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

    res2 = await client.get("/api/aria/ops/session", headers={"Authorization": f"Bearer {token}"})
    assert res2.status_code == 403


@pytest.mark.asyncio
async def test_logout_idempotent_on_invalid_token(client):
    res = await client.post("/api/aria/ops/logout", headers={"Authorization": "Bearer nonsense.value"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


@pytest.mark.asyncio
async def test_logout_idempotent_without_any_token(client):
    res = await client.post("/api/aria/ops/logout")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


# ── /chat ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_without_auth_rejected_and_brain_never_called(client, monkeypatch):
    called = False

    async def _fake_process(*args, **kwargs):
        nonlocal called
        called = True
        return {"response": "should not run"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)
    res = await client.post("/api/aria/ops/chat", json={"message": "salut"})
    assert res.status_code == 403
    assert called is False


@pytest.mark.asyncio
async def test_chat_with_valid_session_calls_brain(client, totp_secret, monkeypatch):
    captured = {}

    async def _fake_process(message, *, visitor_id, public_mode):
        captured["message"] = message
        captured["visitor_id"] = visitor_id
        captured["public_mode"] = public_mode
        return {"response": "ok"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]

    res = await client.post(
        "/api/aria/ops/chat", json={"message": "quel est le statut ?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == {"response": "ok"}
    assert captured == {"message": "quel est le statut ?", "visitor_id": "operator-mobile", "public_mode": False}


@pytest.mark.asyncio
async def test_chat_idempotency_key_prevents_double_execution(client, totp_secret, monkeypatch):
    """Plan requirement (Phase 2): a client-side timeout + server-side success
    mismatch must never re-trigger AriaBrain.process() (and its actions_taken)
    for the same logical message."""
    call_count = 0

    async def _fake_process(message, *, visitor_id, public_mode):
        nonlocal call_count
        call_count += 1
        return {"response": f"call-{call_count}"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    body = {"message": "achete", "idempotency_key": "same-key-123"}
    res1 = await client.post("/api/aria/ops/chat", json=body, headers=headers)
    res2 = await client.post("/api/aria/ops/chat", json=body, headers=headers)

    assert call_count == 1
    assert res1.json() == res2.json() == {"response": "call-1"}


@pytest.mark.asyncio
async def test_chat_without_idempotency_key_always_calls_brain(client, totp_secret, monkeypatch):
    call_count = 0

    async def _fake_process(message, *, visitor_id, public_mode):
        nonlocal call_count
        call_count += 1
        return {"response": f"call-{call_count}"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    body = {"message": "achete"}
    await client.post("/api/aria/ops/chat", json=body, headers=headers)
    await client.post("/api/aria/ops/chat", json=body, headers=headers)

    assert call_count == 2


# ── /version ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_version_is_reachable_without_auth(client):
    res = await client.get("/api/aria/ops/version")
    assert res.status_code == 200
    body = res.json()
    assert body["mobile_api"] == operator_mobile.MOBILE_API_VERSION
    assert body["minimum_mobile_api"] == operator_mobile.MINIMUM_MOBILE_API_VERSION
