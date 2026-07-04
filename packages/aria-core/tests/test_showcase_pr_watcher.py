"""Tests for showcase PR auto-reply watcher."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import showcase_pr_watcher as spw


def test_compose_reply_clarify_when_confused():
    body = spw.compose_reply(
        "Sorry, we don't understand what problem you are facing.",
        target={"pr_number": 37},
    )
    assert "Clarifying our situation" in body
    assert "Server error 500" in body
    assert "019f0522" in body


def test_compose_reply_thanks_on_merge_intent():
    body = spw.compose_reply("Looks good, ready to merge when proof is in.", target={"pr_number": 37})
    assert "reopen PR #37" in body
    assert "validate-showcase" in body


def test_compose_reply_ack_on_incident():
    body = spw.compose_reply(
        "Yes this is a known Privy outage, investigating.",
        target={"pr_number": 37},
    )
    assert "degraded mode" in body.lower()


@pytest.mark.asyncio
async def test_run_showcase_pr_watch_replies_to_external(monkeypatch, tmp_path):
    target = {
        "id": "test",
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    monkeypatch.setattr(spw, "load_watch_targets", lambda: [target])
    monkeypatch.setattr(spw, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())
    monkeypatch.setattr(spw, "append_memory", lambda *a, **k: None)

    posted: list[str] = []

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 9001,
                    "user": {"login": "virtuals-dev"},
                    "body": "We don't understand the blocker.",
                    "created_at": "2026-07-04T18:00:00Z",
                    "html_url": "https://github.com/example/issues/37#issuecomment-9001",
                }
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            posted.append(body)
            return {
                "id": 9002,
                "html_url": "https://github.com/example/issues/37#issuecomment-9002",
            }

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 1
    assert len(result["replied"]) == 1
    assert posted and "Clarifying our situation" in posted[0]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "issue:9001" in state.get("handled", {})


@pytest.mark.asyncio
async def test_run_showcase_pr_watch_skips_our_comments(monkeypatch, tmp_path):
    monkeypatch.setattr(
        spw,
        "load_watch_targets",
        lambda: [
            {
                "owner": "Virtual-Protocol",
                "repo": "acp-cli-demos",
                "pr_number": 37,
                "enabled": True,
                "our_logins": ["GoldenFarFR"],
            }
        ],
    )
    monkeypatch.setattr(spw, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 1,
                    "user": {"login": "GoldenFarFR"},
                    "body": "Closing — premature.",
                    "created_at": "2026-07-04T14:00:00Z",
                    "html_url": "https://example/1",
                }
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            raise AssertionError("should not post")

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 0
    assert result["replied"] == []