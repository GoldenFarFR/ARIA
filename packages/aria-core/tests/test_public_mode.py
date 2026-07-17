import os

import pytest
from fastapi import HTTPException

from aria_core import public_mode
from aria_core.public_mode import (
    is_operator_request,
    operator_action_blocked_reply,
    require_operator,
    resolve_visitor_id,
    skill_allowed_in_public,
)
from aria_core.runtime import settings
from aria_core.testing import AriaRuntimeSettings as Settings


class _FakeClient:
    def __init__(self, host="127.0.0.1"):
        self.host = host


class _FakeRequest:
    def __init__(self, headers=None, host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient(host)


@pytest.fixture(autouse=True)
def _reset_totp_state():
    """_TOTP_FAILS est un dict module-level mutable -- jamais de fuite d'état entre tests."""
    public_mode._TOTP_FAILS.clear()
    yield
    public_mode._TOTP_FAILS.clear()


def test_aria_public_mode_disables_access_gate(monkeypatch):
    monkeypatch.setenv("ARIA_PUBLIC_MODE", "true")
    monkeypatch.setenv("ACCESS_CODE_ENABLED", "true")
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("DEBUG", "false")
    s = Settings()
    assert s.aria_public_mode is True


def test_resolve_visitor_id_from_header():
    class Client:
        host = "127.0.0.1"

    class Req:
        headers = {"X-Visitor-Id": "visitor-abc12345", "User-Agent": "test"}
        client = Client()

    assert resolve_visitor_id(Req()) == "visitor-abc12345"


def test_resolve_visitor_id_fallback_anonymous():
    class Client:
        host = "10.0.0.1"

    class Req:
        headers = {"User-Agent": "Mozilla/5.0"}
        client = Client()

    vid = resolve_visitor_id(Req())
    assert vid.startswith("anon-")


def test_operator_skills_blocked_in_public():
    assert skill_allowed_in_public("faq_content") is True
    assert skill_allowed_in_public("launchpad_select") is True
    assert skill_allowed_in_public("github_sandbox") is False
    assert skill_allowed_in_public("build_optimize") is False
    assert skill_allowed_in_public("develop_repertoire") is False
    assert skill_allowed_in_public("memory_recall") is False
    assert skill_allowed_in_public("marketing_comms") is False


def test_operator_blocked_reply_mentions_code():
    fr = operator_action_blocked_reply("fr")
    assert "code" in fr.lower()
    assert "opérateur" in fr.lower()


def test_operator_blocked_reply_english_branch():
    en = operator_action_blocked_reply("en")
    assert "operator-only" in en
    assert "code" in en.lower()


def test_is_operator_request_false_when_no_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "")
    req = _FakeRequest(headers={"X-Admin-Secret": "anything"})
    assert is_operator_request(req) is False


def test_is_operator_request_false_when_secret_missing_from_request(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    req = _FakeRequest(headers={})
    assert is_operator_request(req) is False


def test_is_operator_request_false_when_secret_wrong(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    req = _FakeRequest(headers={"X-Admin-Secret": "wrong"})
    assert is_operator_request(req) is False


def test_is_operator_request_true_when_secret_matches_and_no_totp(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
    req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t"})
    assert is_operator_request(req) is True


def test_is_operator_request_requires_totp_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "totp-secret")
    monkeypatch.setattr("aria_core.admin_totp.verify_totp", lambda secret, code, **k: False)

    req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"})
    assert is_operator_request(req) is False


def test_is_operator_request_totp_success_clears_fail_history(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "totp-secret")
    monkeypatch.setattr("aria_core.admin_totp.verify_totp", lambda secret, code, **k: code == "123456")

    bad_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"}, host="198.51.100.10")
    assert is_operator_request(bad_req) is False
    assert public_mode._TOTP_FAILS.get("198.51.100.10")

    good_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "123456"}, host="198.51.100.10")
    assert is_operator_request(good_req) is True
    assert "198.51.100.10" not in public_mode._TOTP_FAILS  # succès -> historique d'échecs effacé


def test_is_operator_request_totp_lockout_after_max_fails(monkeypatch):
    """Anti-force-brute : au-delà de _TOTP_MAX_FAILS échecs dans la fenêtre, verrouillé
    MÊME avec un code correct (protège contre une fuite du secret admin)."""
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "totp-secret")
    monkeypatch.setattr("aria_core.admin_totp.verify_totp", lambda secret, code, **k: code == "123456")

    for _ in range(public_mode._TOTP_MAX_FAILS):
        bad_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"}, host="203.0.113.10")
        assert is_operator_request(bad_req) is False

    locked_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "123456"}, host="203.0.113.10")
    assert is_operator_request(locked_req) is False  # verrouillé même avec le bon code


def test_is_operator_request_totp_lockout_is_per_ip(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.setenv("ADMIN_TOTP_SECRET", "totp-secret")
    monkeypatch.setattr("aria_core.admin_totp.verify_totp", lambda secret, code, **k: code == "123456")

    for _ in range(public_mode._TOTP_MAX_FAILS):
        bad_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "000000"}, host="203.0.113.10")
        is_operator_request(bad_req)

    other_ip_req = _FakeRequest(headers={"X-Admin-Secret": "s3cr3t", "X-Admin-Totp": "123456"}, host="192.0.2.10")
    assert is_operator_request(other_ip_req) is True  # une autre IP n'est jamais affectée


def test_require_operator_raises_403_without_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "")
    with pytest.raises(HTTPException) as exc_info:
        require_operator(_FakeRequest())
    assert exc_info.value.status_code == 403
    assert "disabled" in exc_info.value.detail.lower()


def test_require_operator_raises_403_on_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    with pytest.raises(HTTPException) as exc_info:
        require_operator(_FakeRequest(headers={"X-Admin-Secret": "wrong"}))
    assert exc_info.value.status_code == 403
    assert "required" in exc_info.value.detail.lower()


def test_require_operator_passes_silently_on_valid_secret(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_secret", "s3cr3t")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
    require_operator(_FakeRequest(headers={"X-Admin-Secret": "s3cr3t"}))  # ne doit pas lever