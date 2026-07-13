"""ux_watch (tâche #155) — hors-ligne, tout injecté. Vérifie : le gating, le
dédoublonnage quotidien, l'agrégation des micro-détails par viewport, et la sortie
unique (proposition d'issue GitHub, jamais un commit/fusion)."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import ux_watch as uw


class _FakeGitHubClient:
    def __init__(self, *, raises_create=None):
        self.raises_create = raises_create
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        if self.raises_create:
            raise self.raises_create
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/91"}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(uw, "DB_PATH", str(tmp_path / "ux_watch_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    yield


def _findings_vision(mapping: dict[str, list[str]]):
    async def vision_fn(jpeg, instruction):
        # `jpeg` ici est le nom de viewport injecté par le screenshot_fn de test.
        findings = mapping.get(jpeg, [])
        return json.dumps({"findings": findings, "actionable": bool(findings)})
    return vision_fn


def _screenshot_by_viewport_name():
    async def screenshot_fn(url, width, height):
        return "desktop" if width == 1440 else "mobile"
    return screenshot_fn


# ── gating ──────────────────────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_UX_WATCH_ENABLED", raising=False)
    assert uw.ux_watch_enabled() is False


def test_disabled_without_github_token(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    assert uw.ux_watch_enabled() is False


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_UX_WATCH_ENABLED", raising=False)
    result = await uw.run_ux_watch_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await uw.run_ux_watch_cycle()
    assert result == {"outcome": "skipped_paused"}


# ── run_ux_watch_cycle : bout-en-bout ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_cycle_proposes_issue_with_findings_from_both_viewports(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    vision_fn = _findings_vision({
        "desktop": ["Le bouton CTA principal n'a pas de focus clavier visible."],
        "mobile": ["Le header chevauche le premier paragraphe à 375px."],
    })
    fake_github = _FakeGitHubClient()

    result = await uw.run_ux_watch_cycle(
        screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn, github_client=fake_github,
    )

    assert result["outcome"] == "proposed"
    assert result["findings_count"] == 2
    assert len(fake_github.calls) == 1
    assert fake_github.calls[0]["labels"] == ["aria-ux-proposal"]
    assert "focus clavier" in fake_github.calls[0]["body"]
    assert "chevauche" in fake_github.calls[0]["body"]


@pytest.mark.asyncio
async def test_no_findings_does_not_open_github_issue(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    vision_fn = _findings_vision({"desktop": [], "mobile": []})
    fake_github = _FakeGitHubClient()

    result = await uw.run_ux_watch_cycle(
        screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn, github_client=fake_github,
    )

    assert result["outcome"] == "no_findings"
    assert fake_github.calls == []


@pytest.mark.asyncio
async def test_capture_failure_on_one_viewport_does_not_break_the_other(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")

    async def screenshot_fn(url, width, height):
        if width == 1440:
            raise RuntimeError("timeout Playwright")
        return "mobile"

    vision_fn = _findings_vision({"mobile": ["Le focus clavier est invisible sur le lien 'Contact'."]})
    fake_github = _FakeGitHubClient()

    result = await uw.run_ux_watch_cycle(
        screenshot_fn=screenshot_fn, vision_fn=vision_fn, github_client=fake_github,
    )

    assert result["per_viewport"]["desktop"] == "capture_failed"
    assert result["per_viewport"]["mobile"] == "ok"
    assert result["outcome"] == "proposed"
    assert result["findings_count"] == 1


@pytest.mark.asyncio
async def test_vision_unavailable_is_not_a_crash(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")

    async def vision_fn(jpeg, instruction):
        return None

    result = await uw.run_ux_watch_cycle(
        screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn,
    )

    assert result["outcome"] == "no_findings"
    assert result["per_viewport"] == {"desktop": "vision_unavailable", "mobile": "vision_unavailable"}


@pytest.mark.asyncio
async def test_only_one_cycle_per_day(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    vision_fn = _findings_vision({"desktop": [], "mobile": []})

    first = await uw.run_ux_watch_cycle(screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn)
    second = await uw.run_ux_watch_cycle(screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn)

    assert first["outcome"] == "no_findings"
    assert second["outcome"] == "skipped_already_ran_today"


@pytest.mark.asyncio
async def test_github_failure_is_recorded_but_does_not_raise(monkeypatch):
    monkeypatch.setenv("ARIA_UX_WATCH_ENABLED", "1")
    vision_fn = _findings_vision({"desktop": ["Contraste insuffisant sur le sous-titre."], "mobile": []})
    fake_github = _FakeGitHubClient(raises_create=RuntimeError("GitHub indisponible"))

    result = await uw.run_ux_watch_cycle(
        screenshot_fn=_screenshot_by_viewport_name(), vision_fn=vision_fn, github_client=fake_github,
    )

    assert result["outcome"] == "proposal_failed"
    assert result["issue_url"] is None
