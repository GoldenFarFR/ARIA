import os

from aria_core.public_mode import (
    operator_action_blocked_reply,
    resolve_visitor_id,
    skill_allowed_in_public,
)
from aria_core.testing import AriaRuntimeSettings as Settings


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