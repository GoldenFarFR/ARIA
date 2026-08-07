"""Operator mobile routes (Item #201) -- ASGI in-process, real TOTP codes (no
mocked crypto), same doctrine as test_security_hardening.py: offline, no secrets,
no network. Covers the plan's required route coverage: login refused without
TOTP, logout idempotent, /aria/ops/* exempted from the global Privy gate but
individually protected, /status reflects real state, and (Phase 3) the kill-switch
routes -- two factors, anti-replay, idempotence, capped history."""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from aria_core import kill_incident_log, outgoing_pause
from aria_core.admin_totp import generate_secret, totp_code
from app.api.routes import operator_mobile
from app.auth import operator_account as accounts
from app.auth import operator_auth_log as auth_log
from app.auth import operator_session as sessions
from app.auth import operator_totp_replay as totp_replay
from app.auth import rate_limit
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
    monkeypatch.setattr(totp_replay, "DB_PATH", str(tmp_path / "totp_replay.db"))
    monkeypatch.setattr(kill_incident_log, "DB_PATH", str(tmp_path / "aria.db"))
    yield


@pytest.fixture(autouse=True)
def _isolated_pause_state(tmp_path, monkeypatch):
    """Non-negotiable isolation: /stop and /resume drive the REAL kill-switch, and
    outgoing_pause resolves its state file through data_dir() at call time. Without
    this redirect a test run could arm the actual deployed pause state."""
    state_dir = tmp_path / "pause-state"
    state_dir.mkdir()
    monkeypatch.setattr(outgoing_pause, "data_dir", lambda: state_dir)
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """check_rate_limit's store is a module-level dict shared by the whole process:
    the kill-switch bucket is keyed by account id, which is 1 in every test here,
    so without a reset the budget would leak between tests."""
    rate_limit._attempts.clear()
    yield
    rate_limit._attempts.clear()


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


def _login_body(totp_secret, *, password=PASSWORD):
    # totp_secret kept as a parameter (not read here) so every existing call
    # site stays unchanged -- see the 07/08 login-no-longer-needs-TOTP tests
    # below for why the login form itself never sends totp_code anymore.
    return {
        "username": USERNAME,
        "password": password,
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
async def test_login_no_longer_requires_totp(client, totp_secret):
    """07/08 -- operator request ("désactive le totp"): the login FORM no
    longer sends/checks a TOTP code at all -- the app's own biometric lock
    gates re-entry after this one-time login, and the session is now
    effectively permanent (SESSION_TTL). Deliberately UNRELATED to the
    kill-switch TOTP (test_kill_switch_requires_fresh_totp below), which
    stays required on every /stop and /resume call regardless of this
    change."""
    body = _login_body(totp_secret)
    assert "totp_code" not in body
    res = await client.post("/api/aria/ops/login", json=body)
    assert res.status_code == 200


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

    async def _fake_process(message, *, lang, visitor_id, public_mode):
        captured["message"] = message
        captured["lang"] = lang
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
    assert captured == {
        "message": "quel est le statut ?", "lang": "fr",
        "visitor_id": "operator-mobile", "public_mode": False,
    }


@pytest.mark.asyncio
async def test_chat_defaults_to_french_when_lang_absent(client, totp_secret, monkeypatch):
    """Real production bug (30/07): the operator asked in French from the Android app
    and got an English answer, because AriaBrain.process() defaults to LANG_EN and the
    route never passed `lang`. He does not read English, and this app -- unlike his
    Telegram client -- translates nothing."""
    captured = {}

    async def _fake_process(message, *, lang, visitor_id, public_mode):
        captured["lang"] = lang
        return {"response": "ok"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post("/api/aria/ops/chat", json={"message": "combien de positions ?"}, headers=headers)
    assert res.status_code == 200
    assert captured["lang"] == "fr"


@pytest.mark.asyncio
async def test_chat_honours_explicit_lang(client, totp_secret, monkeypatch):
    captured = {}

    async def _fake_process(message, *, lang, visitor_id, public_mode):
        captured["lang"] = lang
        return {"response": "ok"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post(
        "/api/aria/ops/chat", json={"message": "how many positions ?", "lang": "en"}, headers=headers,
    )
    assert res.status_code == 200
    assert captured["lang"] == "en"


@pytest.mark.asyncio
async def test_chat_rejects_unsupported_lang(client, totp_secret, monkeypatch):
    """Bounded to the two real locale constants -- never a free-form string handed
    to the brain."""
    async def _fake_process(*args, **kwargs):
        raise AssertionError("brain must not be reached on an invalid lang")

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)

    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post(
        "/api/aria/ops/chat", json={"message": "hola", "lang": "es"}, headers=headers,
    )
    assert res.status_code == 422


# ── Control-command confabulation guard (real incident, 30/07) ─────────────
# The operator typed "/stop" as a plain chat message and got back "Stop confirmed"
# from the LLM -- a pure confabulation, proven live by a routine trading alert
# that kept arriving right after. These lock the fix: /stop, /resume, /pause,
# /start are intercepted BEFORE the brain, with a fixed reply, never a generated one.

@pytest.mark.asyncio
async def test_chat_stop_command_never_reaches_the_brain(client, totp_secret, monkeypatch):
    async def _fake_process(*args, **kwargs):
        raise AssertionError("brain must not be reached for a /stop chat message")

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post("/api/aria/ops/chat", json={"message": "/stop"}, headers=headers)
    assert res.status_code == 200
    assert "kill-switch" in res.json()["reply"]
    assert outgoing_pause.is_paused() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/resume", "/pause", "/start", "/STOP", "/Stop "])
async def test_chat_intercepts_every_control_command_case_insensitively(
    client, totp_secret, monkeypatch, command,
):
    async def _fake_process(*args, **kwargs):
        raise AssertionError(f"brain must not be reached for {command!r}")

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post("/api/aria/ops/chat", json={"message": command}, headers=headers)
    assert res.status_code == 200
    assert "kill-switch" in res.json()["reply"]


@pytest.mark.asyncio
async def test_chat_stop_reply_respects_lang(client, totp_secret, monkeypatch):
    async def _fake_process(*args, **kwargs):
        raise AssertionError("brain must not be reached")

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post(
        "/api/aria/ops/chat", json={"message": "/stop", "lang": "en"}, headers=headers,
    )
    assert res.status_code == 200
    assert "kill-switch" in res.json()["reply"]
    assert "Telegram" in res.json()["reply"]


@pytest.mark.asyncio
async def test_chat_mentioning_stop_mid_sentence_still_reaches_the_brain(
    client, totp_secret, monkeypatch,
):
    """The guard must never catch a genuine question just because it contains
    the word "stop" -- only an exact /stop-style first word."""
    captured = {}

    async def _fake_process(message, *, lang, visitor_id, public_mode):
        captured["message"] = message
        return {"response": "ok"}

    monkeypatch.setattr(operator_mobile.aria_brain, "process", _fake_process)
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    res = await client.post(
        "/api/aria/ops/chat",
        json={"message": "why is there no stop loss on this token?"},
        headers=headers,
    )
    assert res.status_code == 200
    assert captured["message"] == "why is there no stop loss on this token?"


@pytest.mark.asyncio
async def test_chat_idempotency_key_prevents_double_execution(client, totp_secret, monkeypatch):
    """Plan requirement (Phase 2): a client-side timeout + server-side success
    mismatch must never re-trigger AriaBrain.process() (and its actions_taken)
    for the same logical message."""
    call_count = 0

    async def _fake_process(message, *, lang, visitor_id, public_mode):
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

    async def _fake_process(message, *, lang, visitor_id, public_mode):
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


# ── Phase 3: kill-switch /stop /resume /history ───────────────────────────
# These drive the REAL outgoing_pause state machine (redirected to tmp_path by
# _isolated_pause_state) -- never a mocked guard rail.

# Wrong-but-well-formed code. A random secret producing this exact code within the
# ±1-step window is a ~3-in-a-million coincidence, and the assertions that nothing
# was armed would make such a fluke loud rather than silent.
WRONG_CODE = "000000"
# Deterministically invalid (not 6 digits): verify_totp rejects it on format alone,
# so a loop of many attempts can never accidentally hit a valid code.
NEVER_VALID_CODE = "nope"


def _kill_code(secret, *, step: int = 0) -> str:
    """A DISTINCT but still acceptable code: verify_totp tolerates ±1 step, so
    step=1 gives a second usable code -- needed as soon as one test makes two
    kill-switch calls, since a consumed code can never be reused."""
    return totp_code(secret, at=time.time() + 30 * step)


async def _authed(client, totp_secret) -> dict:
    login = await client.post("/api/aria/ops/login", json=_login_body(totp_secret))
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


@pytest.mark.asyncio
async def test_stop_without_session_rejected_and_nothing_armed(client, totp_secret):
    res = await client.post("/api/aria/ops/stop", json={"totp_code": _kill_code(totp_secret)})
    assert res.status_code == 403
    assert outgoing_pause.is_paused() is False
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_stop_with_session_but_no_totp_rejected(client, totp_secret):
    """The plan's core Phase 3 requirement: the session alone is never enough for
    the one button that can really hurt."""
    headers = await _authed(client, totp_secret)
    res = await client.post("/api/aria/ops/stop", json={}, headers=headers)
    assert res.status_code == 403
    assert outgoing_pause.is_paused() is False
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_stop_with_no_body_at_all_rejected_not_unprocessable(client, totp_secret):
    """A bodyless call must land on the same 403 as a body without a code -- a 422
    would tell a caller the difference between "malformed" and "unauthorized"."""
    headers = await _authed(client, totp_secret)
    res = await client.post("/api/aria/ops/stop", headers=headers)
    assert res.status_code == 403
    assert outgoing_pause.is_paused() is False


@pytest.mark.asyncio
async def test_stop_with_wrong_totp_rejected(client, totp_secret):
    headers = await _authed(client, totp_secret)
    res = await client.post("/api/aria/ops/stop", json={"totp_code": WRONG_CODE}, headers=headers)
    assert res.status_code == 403
    assert outgoing_pause.is_paused() is False
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_stop_arms_state_and_records_incident(client, totp_secret):
    headers = await _authed(client, totp_secret)
    res = await client.post(
        "/api/aria/ops/stop",
        json={"totp_code": _kill_code(totp_secret), "reason": "drill from the phone"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["changed"] is True
    assert body["paused"] is True
    assert outgoing_pause.is_paused() is True

    incidents = await kill_incident_log.list_incidents()
    assert len(incidents) == 1
    assert incidents[0]["event_type"] == kill_incident_log.EVENT_ARMED
    assert incidents[0]["trigger_source"] == kill_incident_log.TRIGGER_MANUAL
    assert incidents[0]["reason"] == "drill from the phone"
    # Internal account id, never the (renameable) username.
    assert incidents[0]["by"] == "mobile:1"


@pytest.mark.asyncio
async def test_replayed_totp_cannot_lift_a_legitimate_stop(client, totp_secret):
    """The dangerous direction of a replay: a captured code must never be reusable
    to undo a STOP the operator genuinely armed."""
    headers = await _authed(client, totp_secret)
    code = _kill_code(totp_secret)

    armed = await client.post("/api/aria/ops/stop", json={"totp_code": code}, headers=headers)
    assert armed.status_code == 200

    replayed = await client.post("/api/aria/ops/resume", json={"totp_code": code}, headers=headers)
    assert replayed.status_code == 403
    assert outgoing_pause.is_paused() is True
    assert [i["event_type"] for i in await kill_incident_log.list_incidents()] == [
        kill_incident_log.EVENT_ARMED
    ]


@pytest.mark.asyncio
async def test_stop_with_replayed_totp_rejected_and_nothing_armed(client, totp_secret):
    """The plan's literal case: a code consumed by any kill-switch call can no
    longer arm the pause either. The first call is a deliberate no-op resume on an
    already-active state -- it changes nothing but still burns the code."""
    headers = await _authed(client, totp_secret)
    code = _kill_code(totp_secret)

    burned = await client.post("/api/aria/ops/resume", json={"totp_code": code}, headers=headers)
    assert burned.status_code == 200
    assert burned.json()["changed"] is False

    res = await client.post("/api/aria/ops/stop", json={"totp_code": code}, headers=headers)
    assert res.status_code == 403
    assert outgoing_pause.is_paused() is False
    assert await kill_incident_log.list_incidents() == []


@pytest.mark.asyncio
async def test_replayed_totp_rejected_on_the_same_route(client, totp_secret):
    headers = await _authed(client, totp_secret)
    code = _kill_code(totp_secret)

    first = await client.post("/api/aria/ops/resume", json={"totp_code": code}, headers=headers)
    assert first.status_code == 200

    second = await client.post("/api/aria/ops/resume", json={"totp_code": code}, headers=headers)
    assert second.status_code == 403


@pytest.mark.asyncio
async def test_stop_idempotent_when_already_paused(client, totp_secret):
    headers = await _authed(client, totp_secret)

    first = await client.post(
        "/api/aria/ops/stop", json={"totp_code": _kill_code(totp_secret)}, headers=headers,
    )
    assert first.json()["changed"] is True

    second = await client.post(
        "/api/aria/ops/stop", json={"totp_code": _kill_code(totp_secret, step=1)}, headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert second.json()["paused"] is True
    # A retry must not pollute the audit trail with a second "armed".
    assert len(await kill_incident_log.list_incidents()) == 1


@pytest.mark.asyncio
async def test_double_resume_retry_does_not_break(client, totp_secret):
    """Network-retry shape: the pause is armed out-of-band, then /resume is called
    twice with two fresh codes -- second call is a clean no-op, not an error, and
    logs nothing extra."""
    outgoing_pause.pause(by="test", reason="armed out of band")
    headers = await _authed(client, totp_secret)

    first = await client.post(
        "/api/aria/ops/resume", json={"totp_code": _kill_code(totp_secret)}, headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["changed"] is True
    assert first.json()["paused"] is False

    second = await client.post(
        "/api/aria/ops/resume", json={"totp_code": _kill_code(totp_secret, step=1)}, headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert outgoing_pause.is_paused() is False
    assert [i["event_type"] for i in await kill_incident_log.list_incidents()] == [
        kill_incident_log.EVENT_LIFTED
    ]


@pytest.mark.asyncio
async def test_resume_repairs_an_unreadable_pause_state(client, totp_secret):
    """A corrupted pause_state.json reads as paused=False but freezes spending
    fail-closed -- treating it as "already resumed" would leave no way to unfreeze
    the money from this channel."""
    (outgoing_pause.data_dir() / "pause_state.json").write_text("{ not json", encoding="utf-8")
    assert outgoing_pause.pause_status()["readable"] is False
    assert outgoing_pause.is_paused(strict=True) is True

    headers = await _authed(client, totp_secret)
    res = await client.post(
        "/api/aria/ops/resume", json={"totp_code": _kill_code(totp_secret)}, headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["changed"] is True
    assert res.json()["readable"] is True
    assert outgoing_pause.is_paused(strict=True) is False
    assert [i["event_type"] for i in await kill_incident_log.list_incidents()] == [
        kill_incident_log.EVENT_LIFTED
    ]


@pytest.mark.asyncio
async def test_incident_log_failure_never_blocks_the_real_action(client, totp_secret, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("incident log unavailable")

    monkeypatch.setattr(operator_mobile.kill_incident_log, "record_incident", _boom)
    headers = await _authed(client, totp_secret)

    res = await client.post(
        "/api/aria/ops/stop", json={"totp_code": _kill_code(totp_secret)}, headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["paused"] is True
    assert outgoing_pause.is_paused() is True


@pytest.mark.asyncio
async def test_kill_switch_totp_attempts_are_throttled(client, totp_secret):
    """Bounded brute-force surface: verify_totp accepts 3 codes in 10^6 per attempt,
    so unlimited guessing with a stolen session would be a real hole. The window
    always releases on its own -- never a hard lock on a fallback channel."""
    headers = await _authed(client, totp_secret)

    for _ in range(operator_mobile.KILL_SWITCH_MAX_ATTEMPTS):
        res = await client.post(
            "/api/aria/ops/stop", json={"totp_code": NEVER_VALID_CODE}, headers=headers,
        )
        assert res.status_code == 403

    throttled = await client.post(
        "/api/aria/ops/stop", json={"totp_code": NEVER_VALID_CODE}, headers=headers,
    )
    assert throttled.status_code == 429
    assert outgoing_pause.is_paused() is False


@pytest.mark.asyncio
async def test_legacy_secret_path_needs_no_mobile_totp(client, monkeypatch):
    """Assumed difference in level (plan): the legacy caller already presented its
    own X-Admin-Totp secret, and serves scripts rather than a human app."""
    monkeypatch.setattr(settings, "admin_api_secret", "test-legacy-secret")
    res = await client.post(
        "/api/aria/ops/stop", json={}, headers={"X-Admin-Secret": "test-legacy-secret"},
    )
    assert res.status_code == 200
    assert res.json()["changed"] is True
    assert outgoing_pause.is_paused() is True
    assert (await kill_incident_log.list_incidents())[0]["by"] == "ops-api:legacy"


# ── /history ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_requires_auth(client):
    res = await client.get("/api/aria/ops/history")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_history_limit_capped_server_side(client, totp_secret):
    for i in range(3):
        await kill_incident_log.record_incident(
            event_type=kill_incident_log.EVENT_ARMED,
            trigger_source=kill_incident_log.TRIGGER_MANUAL,
            by="test", reason=f"incident {i}",
        )
    headers = await _authed(client, totp_secret)

    res = await client.get("/api/aria/ops/history?limit=100000", headers=headers)
    assert res.status_code == 200
    assert res.json()["limit"] == operator_mobile.HISTORY_MAX_LIMIT
    assert len(res.json()["incidents"]) == 3


@pytest.mark.asyncio
async def test_history_absurd_limit_clamped_to_at_least_one(client, totp_secret):
    await kill_incident_log.record_incident(
        event_type=kill_incident_log.EVENT_ARMED,
        trigger_source=kill_incident_log.TRIGGER_MANUAL, by="test", reason="only one",
    )
    headers = await _authed(client, totp_secret)

    res = await client.get("/api/aria/ops/history?limit=-5", headers=headers)
    assert res.status_code == 200
    assert res.json()["limit"] == 1
    assert len(res.json()["incidents"]) == 1


@pytest.mark.asyncio
async def test_history_default_limit_when_unspecified(client, totp_secret):
    headers = await _authed(client, totp_secret)
    res = await client.get("/api/aria/ops/history", headers=headers)
    assert res.status_code == 200
    assert res.json()["limit"] == operator_mobile.HISTORY_DEFAULT_LIMIT
    assert res.json()["incidents"] == []


@pytest.mark.asyncio
async def test_register_push_token_requires_session(client):
    res = await client.post("/api/aria/ops/push-token", json={"token": "ExponentPushToken[x]"})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_register_push_token_persists(client, totp_secret):
    from aria_core.push_tokens import list_push_tokens

    headers = await _authed(client, totp_secret)
    res = await client.post(
        "/api/aria/ops/push-token",
        json={"token": "ExponentPushToken[persist-1]", "installation_id": "dev-1"},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert "ExponentPushToken[persist-1]" in await list_push_tokens()
