"""Tests for showcase PR auto-reply watcher.

Comportement (session 08/07) : ARIA repond SEULE uniquement sur un feu vert net et sans
risque (merge/LGTM sans negation, question ni sujet technique). Tout le reste -> elle poste
un court message public de passage de relai ET prepare un ping operateur (elle n'invente ni
ne tranche rien). Toute reponse postee porte la signature de transparence, sans em-dash.
"""
from __future__ import annotations

import json

import pytest

from aria_core.skills import showcase_pr_watcher as spw

_EM_DASH = "—"


def test_decide_reply_auto_on_clear_green_light():
    action, body = spw.decide_reply(
        "Looks good, ready to merge when proof is in.", target={"pr_number": 37}
    )
    assert action == "reply"
    assert "reopen PR #37" in body
    assert "validate-showcase" in body


def test_decide_reply_handover_on_question():
    action, _ = spw.decide_reply(
        "Can you explain what the blocker is?", target={"pr_number": 37}
    )
    assert action == "handover"


def test_decide_reply_handover_on_technical_fix():
    # Le vrai cas PR#37 : le mainteneur donne un correctif technique -> ARIA passe la main
    # (elle ne doit surtout pas repondre un template a cote de la plaque).
    action, _ = spw.decide_reply(
        "The 500 is an unregistered signer. Re-run acp agent add-signer and check signer-status.",
        target={"pr_number": 37},
    )
    assert action == "handover"


def test_decide_reply_handover_on_negation_merge():
    # "not ready to merge" ne doit JAMAIS declencher la reponse "on rouvre".
    action, _ = spw.decide_reply("This is not ready to merge yet.", target={"pr_number": 37})
    assert action == "handover"


def test_decide_reply_handover_on_incident_mention():
    action, _ = spw.decide_reply(
        "Yes this is a known Privy outage, investigating.", target={"pr_number": 37}
    )
    assert action == "handover"


def test_reply_carries_transparency_signature_without_em_dash():
    _, body = spw.decide_reply("LGTM, ready to merge.", target={"pr_number": 37})
    signed = spw._sign(body)
    assert "autonomous AI owned by GoldenFarFR" in signed
    assert _EM_DASH not in signed


def test_public_templates_have_no_em_dash_or_unverified_500():
    for tpl in (spw._THANKS_REOPEN_TEMPLATE, spw._HANDOVER_TEMPLATE, spw._OPERATOR_DRAFT_TEMPLATE):
        assert _EM_DASH not in tpl
    # Plus aucune affirmation de "Server error 500" assenee comme fait courant.
    assert "Server error 500" not in spw._THANKS_REOPEN_TEMPLATE
    assert "500" not in spw._HANDOVER_TEMPLATE


def test_handover_template_is_public_relay_message():
    assert "handing it over to my operator" in spw._HANDOVER_TEMPLATE.lower()


def test_is_external_comment_with_sim_marker():
    target = {"test_reviewer_marker": "[SIM reviewer]", "our_logins": ["GoldenFarFR"]}
    row = {"author": "GoldenFarFR", "body": "[SIM reviewer] please take a look"}
    assert spw._is_external_comment(row, {"goldenfarfr"}, target)


def _fake_env(monkeypatch, tmp_path, target):
    monkeypatch.setattr(spw, "load_watch_targets", lambda: [target])
    monkeypatch.setattr(spw, "_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())
    monkeypatch.setattr(spw, "append_memory", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_run_hands_over_when_unclear(monkeypatch, tmp_path):
    target = {
        "id": "test",
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    _fake_env(monkeypatch, tmp_path, target)
    posted: list[str] = []

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 9001,
                    "user": {"login": "virtuals-dev"},
                    "body": "We don't understand the blocker. Can you explain?",
                    "created_at": "2026-07-08T18:00:00Z",
                    "html_url": "https://github.com/example/issues/37#issuecomment-9001",
                }
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def list_review_comments(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            posted.append(body)
            return {"id": 9002, "html_url": "https://github.com/example/issues/37#issuecomment-9002"}

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 1
    assert result["replied"] == []
    assert len(result["handed_over"]) == 1
    # Le message public est le relai signe, pas un template a cote de la plaque.
    assert posted and "handing it over to my operator" in posted[0].lower()
    assert "@GoldenFarFR" in posted[0]  # l'operateur est tague (il reprend la main)
    assert "autonomous AI owned by GoldenFarFR" in posted[0]
    ho = result["handed_over"][0]
    assert ho["handover"] is True
    assert ho["suggested_draft"]
    assert "understand the blocker" in ho["comment_excerpt"]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "issue:9001" in state.get("handled", {})


@pytest.mark.asyncio
async def test_run_hands_over_on_inline_review_comment(monkeypatch, tmp_path):
    # Cas reel PR#37 (09/07) : un relecteur laisse une suggestion INLINE sur une ligne de
    # diff (path=showcase.json), sans jamais poster de commentaire "issue" ni de corps de
    # review global -> avant le fix, ce commentaire etait invisible pour le watcher.
    target = {
        "id": "test",
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    _fake_env(monkeypatch, tmp_path, target)
    posted: list[str] = []

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return []

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def list_review_comments(self, owner, repo, pull_number):
            return [
                {
                    "id": 3557711722,
                    "user": {"login": "ytoast"},
                    "body": "Nit: trim topics to the recognized taxonomy.",
                    "path": "showcase/aria-vanguard-zhc/showcase.json",
                    "created_at": "2026-07-09T12:00:00Z",
                    "html_url": "https://github.com/example/pull/37#discussion_r3557711722",
                }
            ]

        async def create_issue_comment(self, owner, repo, issue_number, body):
            posted.append(body)
            return {"id": 9101, "html_url": "https://github.com/example/issues/37#issuecomment-9101"}

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 1
    assert result["replied"] == []
    assert len(result["handed_over"]) == 1
    assert posted and "handing it over to my operator" in posted[0].lower()
    ho = result["handed_over"][0]
    assert ho["handover"] is True
    assert "showcase.json" in ho["comment_excerpt"]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "review_comment:3557711722" in state.get("handled", {})


@pytest.mark.asyncio
async def test_run_auto_replies_on_green_light(monkeypatch, tmp_path):
    target = {
        "id": "test",
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    _fake_env(monkeypatch, tmp_path, target)
    posted: list[str] = []

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 7001,
                    "user": {"login": "virtuals-dev"},
                    "body": "Looks good, ready to merge.",
                    "created_at": "2026-07-08T18:00:00Z",
                    "html_url": "https://github.com/example/issues/37#issuecomment-7001",
                }
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def list_review_comments(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            posted.append(body)
            return {"id": 7002, "html_url": "https://github.com/example/issues/37#issuecomment-7002"}

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 1
    assert len(result["replied"]) == 1
    assert result["handed_over"] == []
    assert posted and "reopen PR #37" in posted[0]
    assert "autonomous AI owned by GoldenFarFR" in posted[0]
    assert _EM_DASH not in posted[0]


@pytest.mark.asyncio
async def test_run_skips_when_human_replied_first(monkeypatch, tmp_path):
    target = {
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    _fake_env(monkeypatch, tmp_path, target)

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 100,
                    "user": {"login": "virtuals-dev"},
                    "body": "Can you clarify the 500?",
                    "created_at": "2026-07-04T18:00:00Z",
                    "html_url": "https://example/100",
                },
                {
                    "id": 101,
                    "user": {"login": "GoldenFarFR"},
                    "body": "Sure, here is the stack trace from our side.",
                    "created_at": "2026-07-04T18:05:00Z",
                    "html_url": "https://example/101",
                },
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def list_review_comments(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            raise AssertionError("should not post when human already replied")

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 0
    assert result["replied"] == []
    assert result["handed_over"] == []
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state.get("handled", {}).get("issue:100") == "human-replied"


@pytest.mark.asyncio
async def test_run_skips_our_comments(monkeypatch, tmp_path):
    target = {
        "owner": "Virtual-Protocol",
        "repo": "acp-cli-demos",
        "pr_number": 37,
        "enabled": True,
        "our_logins": ["GoldenFarFR"],
    }
    _fake_env(monkeypatch, tmp_path, target)

    class FakeClient:
        async def list_issue_comments(self, owner, repo, issue_number):
            return [
                {
                    "id": 1,
                    "user": {"login": "GoldenFarFR"},
                    "body": "Closing, premature.",
                    "created_at": "2026-07-04T14:00:00Z",
                    "html_url": "https://example/1",
                }
            ]

        async def list_pull_reviews(self, owner, repo, pull_number):
            return []

        async def list_review_comments(self, owner, repo, pull_number):
            return []

        async def create_issue_comment(self, owner, repo, issue_number, body):
            raise AssertionError("should not post")

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    result = await spw.run_showcase_pr_watch()
    assert result["new_external"] == 0
    assert result["replied"] == []
    assert result["handed_over"] == []


def test_wants_showcase_pr_repair_matches():
    assert spw.wants_showcase_pr_repair("showcase pr repair")
    assert spw.wants_showcase_pr_repair("corrige la reponse showcase")
    assert spw.wants_showcase_pr_repair("corrige la réponse du PR")
    assert not spw.wants_showcase_pr_repair("showcase pr watch")
    assert not spw.wants_showcase_pr_repair("bonjour")


@pytest.mark.asyncio
async def test_repair_edits_last_comment_to_handover(monkeypatch, tmp_path):
    # Etat : ARIA a deja poste un commentaire (le mauvais). La reparation l'EDITE.
    state = {
        "handled": {"issue:9001": "9002"},
        "replies": [
            {
                "target": "acp-showcase-37",
                "owner": "Virtual-Protocol",
                "repo": "acp-cli-demos",
                "pr_number": 37,
                "reply_id": 9002,
                "reply_url": "https://github.com/x/issues/37#issuecomment-9002",
                "handover": False,
            }
        ],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(spw, "_STATE_PATH", state_path)
    monkeypatch.setattr(spw, "load_watch_targets", lambda: [])
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)
    monkeypatch.setattr(spw, "append_memory", lambda *a, **k: None)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())
    edits: list[tuple] = []

    class FakeClient:
        async def edit_issue_comment(self, owner, repo, comment_id, body):
            edits.append((owner, repo, comment_id, body))
            return {
                "id": comment_id,
                "html_url": f"https://github.com/x/issues/37#issuecomment-{comment_id}",
            }

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    report = await spw.repair_last_reply()
    assert report["ok"] is True
    assert report["edited"]["id"] == 9002
    # Le nouveau corps = message de relai signe, taguant l'operateur.
    assert edits and edits[0][2] == 9002
    new_body = edits[0][3]
    assert "handing it over to my operator" in new_body.lower()
    assert "@GoldenFarFR" in new_body
    assert "autonomous AI owned by GoldenFarFR" in new_body
    assert _EM_DASH not in new_body
    # Etat marque comme repare.
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["replies"][-1].get("repaired_at")


@pytest.mark.asyncio
async def test_repair_reports_error_when_nothing_posted(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"handled": {}, "replies": []}), encoding="utf-8")
    monkeypatch.setattr(spw, "_STATE_PATH", state_path)
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())
    report = await spw.repair_last_reply()
    assert report["ok"] is False
    assert report["errors"]


@pytest.mark.asyncio
async def test_repair_derives_owner_repo_from_url_legacy_entry(monkeypatch, tmp_path):
    # Entree d'etat LEGACY : postee par l'ancien code, sans owner/repo -> derives de l'URL.
    state = {
        "handled": {"issue:4913933229": "4913933229"},
        "replies": [
            {
                "target": "acp-showcase-37",
                "reply_id": 4913933229,
                "reply_url": "https://github.com/Virtual-Protocol/acp-cli-demos/pull/37#issuecomment-4913933229",
            }
        ],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(spw, "_STATE_PATH", state_path)
    monkeypatch.setattr(spw, "load_watch_targets", lambda: [])
    monkeypatch.setattr("aria_core.skills.showcase_pr_watcher.github_configured", lambda: True)
    monkeypatch.setattr(spw, "append_memory", lambda *a, **k: None)

    class FakeSettings:
        github_token = "test-token"

    monkeypatch.setattr(spw, "settings", FakeSettings())
    edits: list[tuple] = []

    class FakeClient:
        async def edit_issue_comment(self, owner, repo, comment_id, body):
            edits.append((owner, repo, comment_id, body))
            return {"id": comment_id, "html_url": "https://github.com/x#c"}

    monkeypatch.setattr(spw, "GitHubClient", lambda token: FakeClient())

    report = await spw.repair_last_reply()
    assert report["ok"] is True
    assert edits[0][0] == "Virtual-Protocol" and edits[0][1] == "acp-cli-demos"
    assert edits[0][2] == 4913933229
    assert "@GoldenFarFR" in edits[0][3]
