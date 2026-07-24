"""Veille des thèses VC crypto (#58) — synthèse LLM + digest opérateur + proposition
d'issue GitHub SEULEMENT si jugé durable. Aucun réseau réel : llm/notifier/github_client
tous injectés."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import vc_intelligence as vci


class _FakeGitHubClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/99"}


def _good_llm(summary="Les VC observés convergent sur les agents onchain.",
              durable=False, title="", body=""):
    async def llm(prompt, system, *, max_tokens=500, model=None, depth=None):
        return json.dumps({
            "summary": summary, "durable": durable,
            "proposal_title": title, "proposal_body": body,
        })
    return llm


ITEMS = [
    {"topic": "@a16zcrypto", "text": "We're excited about onchain agent infrastructure."},
    {"topic": "@paradigm", "text": "Agent-to-agent payments are the next big unlock."},
]


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_VC_INTELLIGENCE_ENABLED", raising=False)
    assert vci.vc_intelligence_enabled() is False


def test_enabled_via_env(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")
    assert vci.vc_intelligence_enabled() is True


@pytest.mark.asyncio
async def test_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_VC_INTELLIGENCE_ENABLED", raising=False)
    result = await vci.run_vc_intelligence_cycle(items=ITEMS, llm=_good_llm())
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_no_items_is_a_noop(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")
    result = await vci.run_vc_intelligence_cycle(items=[], llm=_good_llm())
    assert result == {"outcome": "no_items"}


@pytest.mark.asyncio
async def test_pushes_digest_but_no_issue_when_not_durable(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")
    sent = []

    async def notifier(text):
        sent.append(text)

    gh = _FakeGitHubClient()
    result = await vci.run_vc_intelligence_cycle(
        items=ITEMS, llm=_good_llm(durable=False), notifier=notifier, github_client=gh,
    )
    assert result["outcome"] == "ok"
    assert result["durable"] is False
    assert result["issue_url"] is None
    assert len(sent) == 1
    assert "Veille VC" in sent[0]
    assert gh.calls == []  # jamais de proposition si non durable


@pytest.mark.asyncio
async def test_proposes_issue_when_durable(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")

    async def notifier(text):
        pass

    gh = _FakeGitHubClient()
    result = await vci.run_vc_intelligence_cycle(
        items=ITEMS,
        llm=_good_llm(
            durable=True,
            title="Convergence agents onchain",
            body="Plusieurs VC signalent un intérêt fort pour les agents onchain.",
        ),
        notifier=notifier,
        github_client=gh,
    )
    assert result["outcome"] == "ok"
    assert result["durable"] is True
    assert result["issue_url"] == "https://github.com/GoldenFarFR/ARIA/issues/99"
    assert len(gh.calls) == 1
    assert gh.calls[0]["labels"] == ["aria-strategy-proposal"]
    assert "never a rewrite" in gh.calls[0]["body"]


@pytest.mark.asyncio
async def test_notifier_failure_does_not_crash_cycle(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")

    async def boom(text):
        raise RuntimeError("telegram down")

    result = await vci.run_vc_intelligence_cycle(items=ITEMS, llm=_good_llm(), notifier=boom)
    assert result["outcome"] == "ok"  # le cycle se termine proprement malgré l'échec d'envoi


@pytest.mark.asyncio
async def test_llm_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")

    async def empty_llm(prompt, system, **kw):
        return None

    result = await vci.run_vc_intelligence_cycle(items=ITEMS, llm=empty_llm)
    assert result == {"outcome": "llm_unavailable"}


@pytest.mark.asyncio
async def test_parse_failed_on_bad_json(monkeypatch):
    monkeypatch.setenv("ARIA_VC_INTELLIGENCE_ENABLED", "true")

    async def bad_llm(prompt, system, **kw):
        return "not json"

    result = await vci.run_vc_intelligence_cycle(items=ITEMS, llm=bad_llm)
    assert result == {"outcome": "parse_failed"}


def test_format_vc_items_for_prompt_caps_and_labels():
    formatted = vci._format_vc_items_for_prompt(ITEMS)
    assert "@a16zcrypto" in formatted
    assert "@paradigm" in formatted
