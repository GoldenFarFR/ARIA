"""Revue de performance ARIA par Claude — ancrée sur ses vraies données mesurées,
hors-ligne, tout injecté. Vérifie : gating, throttle, fail-closed par source, les deux
canaux de sortie (relais + proposition GitHub jamais un commit/fusion)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aria_core import relay_chat
from aria_core.skills import claude_mentor as cm


class _FakeGitHubClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/42"}


def _good_snapshot_mocks(monkeypatch):
    monkeypatch.setattr(
        "aria_core.vc_predictions.metrics",
        AsyncMock(return_value={
            "closed": 12, "buy_count": 8, "hit_rate": 0.625,
            "avg_win_pct": 55.0, "avg_loss_pct": -18.0, "avoid_count": 5,
        }),
    )
    monkeypatch.setattr(
        "aria_core.paper_trader.portfolio_summary",
        AsyncMock(return_value={
            "closed_trades": 4, "win_rate": 50.0, "return_pct": 3.2, "realized_pnl": 1200.0,
        }),
    )
    monkeypatch.setattr(
        "aria_core.onchain.sepolia_autonomous.autonomous_status",
        AsyncMock(return_value={
            "cycles_total": 9, "tx_count": 3, "error_count": 0, "hesitation_count": 1,
        }),
    )


def _good_llm(remark="Ton hit-rate BUY est de 62% mais tes pertes moyennes dépassent tes gains.",
              durable=False, title="", body=""):
    async def llm(prompt, system, *, max_tokens=700, model=None, depth=None,
                  provider=None, fallback_provider=None, fallback_model=None):
        return json.dumps({
            "remark": remark, "durable": durable,
            "proposal_title": title, "proposal_body": body,
        })
    return llm


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "DB_PATH", str(tmp_path / "mentor_test.db"))
    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay_test.db"))
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


# ── Gating ────────────────────────────────────────────────────────────────

def test_disabled_without_relay_token(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    assert cm.claude_mentor_enabled() is False


def test_disabled_without_explicit_flag():
    assert cm.claude_mentor_enabled() is False


def test_enabled_when_relay_and_flag_both_set(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "true")
    assert cm.claude_mentor_enabled() is True


# ── Cycle : gating / paused / donnees insuffisantes ─────────────────────────

@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled():
    result = await cm.run_claude_mentor_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await cm.run_claude_mentor_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_cycle_insufficient_data(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    monkeypatch.setattr(
        "aria_core.vc_predictions.metrics",
        AsyncMock(return_value={"closed": 0}),
    )
    monkeypatch.setattr(
        "aria_core.paper_trader.portfolio_summary",
        AsyncMock(return_value={"closed_trades": 0}),
    )
    monkeypatch.setattr(
        "aria_core.onchain.sepolia_autonomous.autonomous_status",
        AsyncMock(return_value={"cycles_total": 0}),
    )
    result = await cm.run_claude_mentor_cycle()
    assert result == {"outcome": "insufficient_data"}


@pytest.mark.asyncio
async def test_snapshot_fails_closed_per_source_never_crashes(monkeypatch):
    """Une source en panne ne doit ni casser le cycle ni faire disparaitre les autres."""
    monkeypatch.setattr(
        "aria_core.vc_predictions.metrics", AsyncMock(side_effect=RuntimeError("db locked")),
    )
    monkeypatch.setattr(
        "aria_core.paper_trader.portfolio_summary",
        AsyncMock(return_value={"closed_trades": 3, "win_rate": 66.0}),
    )
    monkeypatch.setattr(
        "aria_core.onchain.sepolia_autonomous.autonomous_status",
        AsyncMock(return_value={"cycles_total": 0}),
    )
    snapshot = await cm._gather_performance_snapshot()
    assert "error" in snapshot["vc_predictions"]
    assert snapshot["paper_trading"]["closed_trades"] == 3
    assert cm._has_enough_signal(snapshot) is True
    text = cm._format_snapshot_for_prompt(snapshot)
    assert "unavailable" in text
    assert "db locked" in text


# ── Cycle : sortie relais + proposition durable ─────────────────────────────

@pytest.mark.asyncio
async def test_cycle_posts_remark_to_relay_no_github_when_not_durable(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)

    sent = []

    async def fake_send_message(text):
        sent.append(text)
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", fake_send_message)
    fake_gh = _FakeGitHubClient()

    result = await cm.run_claude_mentor_cycle(llm=_good_llm(), github_client=fake_gh)

    assert result["outcome"] == "ok"
    assert result["durable"] is False
    assert result["issue_url"] is None
    assert sent and "hit-rate" in sent[0]
    assert fake_gh.calls == []

    messages = await relay_chat.recent_messages()
    assert messages[-1]["sender"] == "claude"


@pytest.mark.asyncio
async def test_cycle_opens_knowledge_issue_when_durable_never_a_commit(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", AsyncMock(return_value=True))
    fake_gh = _FakeGitHubClient()

    result = await cm.run_claude_mentor_cycle(
        llm=_good_llm(
            durable=True,
            title="Corriger le biais gains/pertes du sizing",
            body="Résumé: ... Fichier cible: knowledge/methodologie.yaml",
        ),
        github_client=fake_gh,
    )

    assert result["outcome"] == "ok"
    assert result["durable"] is True
    assert result["issue_url"] == "https://github.com/GoldenFarFR/ARIA/issues/42"
    assert len(fake_gh.calls) == 1
    call = fake_gh.calls[0]
    assert call["labels"] == ["aria-knowledge-proposal"]
    assert "human review required" in call["body"]
    assert not hasattr(fake_gh, "create_pull_request")
    assert not hasattr(fake_gh, "create_commit")


@pytest.mark.asyncio
async def test_cycle_llm_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)

    async def broken_llm(prompt, system, **kw):
        return None

    result = await cm.run_claude_mentor_cycle(llm=broken_llm)
    assert result == {"outcome": "llm_unavailable"}


@pytest.mark.asyncio
async def test_cycle_parse_failed(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)

    async def broken_llm(prompt, system, **kw):
        return "pas du json"

    result = await cm.run_claude_mentor_cycle(llm=broken_llm)
    assert result == {"outcome": "parse_failed"}


@pytest.mark.asyncio
async def test_cycle_empty_remark_not_posted(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)

    sent = []
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.send_message",
        AsyncMock(side_effect=lambda t: sent.append(t)),
    )

    result = await cm.run_claude_mentor_cycle(llm=_good_llm(remark=""))
    assert result == {"outcome": "empty_remark"}
    assert sent == []


# ── Throttle ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_throttled_after_a_successful_run(monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", AsyncMock(return_value=True))

    first = await cm.run_claude_mentor_cycle(llm=_good_llm())
    assert first["outcome"] == "ok"

    second = await cm.run_claude_mentor_cycle(llm=_good_llm())
    assert second["outcome"] == "throttled"
    assert second["hours_since_last"] < cm.MIN_INTERVAL_HOURS


# ── routage explicite Sonnet 5 + secours Haiku (17/07) ──────────────────────

@pytest.mark.asyncio
async def test_cycle_uses_global_provider_no_openrouter_override(monkeypatch):
    """19/07 -- décision opérateur explicite ("bascule sur spark et quand spark sera
    vide en valeur on passera sur anthropique comme prévu") : l'override Haiku/Sonnet
    via OpenRouter (retenu le 17/07 après une revue de raisonnement profond réelle)
    a été retiré -- ce cycle utilise désormais le provider/fallback global (Spark),
    comme tout le reste d'ARIA."""
    monkeypatch.setenv("ARIA_CLAUDE_MENTOR_ENABLED", "1")
    _good_snapshot_mocks(monkeypatch)

    captured = {}

    async def capturing_llm(prompt, system, **kwargs):
        captured.update(kwargs)
        return json.dumps({"remark": "ok", "durable": False, "proposal_title": "", "proposal_body": ""})

    await cm.run_claude_mentor_cycle(llm=capturing_llm)
    assert "provider" not in captured
    assert "model" not in captured
    assert "fallback_provider" not in captured
    assert "fallback_model" not in captured
    assert captured.get("max_tokens") == 900
