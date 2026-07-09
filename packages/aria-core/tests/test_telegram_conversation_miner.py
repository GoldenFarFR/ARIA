"""Mineur de conversations opérateur/ARIA — hors-ligne, tout injecté. Vérifie : gating,
throttle, watermark (ne remine jamais deux fois les mêmes messages), et surtout le filet
de sécurité anti-secret avant toute publication d'issue (une création d'issue ne passe
PAS par le scan gitleaks de la CI, contrairement à un push)."""
from __future__ import annotations

import json

import pytest

from aria_core import relay_chat
from aria_core.skills import telegram_conversation_miner as tcm


class _FakeGitHubClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/99"}


def _good_llm(durable=False, title="", body=""):
    async def llm(prompt, system, *, max_tokens=700):
        return json.dumps({"durable": durable, "proposal_title": title, "proposal_body": body})
    return llm


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.paths.aria_db_path", lambda: tmp_path / "miner_test.db")
    monkeypatch.setattr(relay_chat, "DB_PATH", str(tmp_path / "relay_test.db"))
    monkeypatch.setenv("ARIA_RELAY_ACCESS_TOKEN", "secret123")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


async def _seed_exchange(n_pairs: int) -> None:
    for i in range(n_pairs):
        await relay_chat.log_message("operator", f"question numero {i}")
        await relay_chat.log_message("aria", f"reponse numero {i}")


# ── Gating ────────────────────────────────────────────────────────────────

def test_disabled_without_relay_token(monkeypatch):
    monkeypatch.delenv("ARIA_RELAY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    assert tcm.telegram_miner_enabled() is False


def test_disabled_without_github_token(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    assert tcm.telegram_miner_enabled() is False


def test_disabled_without_explicit_flag():
    assert tcm.telegram_miner_enabled() is False


def test_enabled_when_everything_set(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "true")
    assert tcm.telegram_miner_enabled() is True


# ── Cycle : gating / paused / signal insuffisant ────────────────────────────

@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled():
    result = await tcm.run_telegram_miner_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await tcm.run_telegram_miner_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_cycle_nothing_new(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    result = await tcm.run_telegram_miner_cycle()
    assert result == {"outcome": "nothing_new"}


@pytest.mark.asyncio
async def test_cycle_insufficient_signal_below_minimum_exchanges(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(1)  # 2 messages, sous _MIN_NEW_MESSAGES
    result = await tcm.run_telegram_miner_cycle()
    assert result["outcome"] == "insufficient_signal"


# ── Cycle : proposition durable / non durable ───────────────────────────────

@pytest.mark.asyncio
async def test_cycle_not_durable_never_opens_issue(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)
    fake_gh = _FakeGitHubClient()

    result = await tcm.run_telegram_miner_cycle(llm=_good_llm(durable=False), github_client=fake_gh)

    assert result == {"outcome": "not_durable"}
    assert fake_gh.calls == []


@pytest.mark.asyncio
async def test_cycle_durable_opens_knowledge_issue_never_a_commit(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)
    fake_gh = _FakeGitHubClient()

    result = await tcm.run_telegram_miner_cycle(
        llm=_good_llm(
            durable=True,
            title="Préférence opérateur : toujours confirmer avant un déploiement VPS",
            body="Résumé abstrait de la préférence observée, sans citation directe.",
        ),
        github_client=fake_gh,
    )

    assert result["outcome"] == "ok"
    assert result["issue_url"] == "https://github.com/GoldenFarFR/ARIA/issues/99"
    assert len(fake_gh.calls) == 1
    call = fake_gh.calls[0]
    assert call["labels"] == ["aria-knowledge-proposal"]
    assert "revue humaine requise" in call["body"]
    assert not hasattr(fake_gh, "create_pull_request")
    assert not hasattr(fake_gh, "create_commit")


# ── Filet de sécurité anti-secret (le point le plus important de ce module) ─

@pytest.mark.asyncio
async def test_secret_looking_title_blocks_publication(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)
    fake_gh = _FakeGitHubClient()

    result = await tcm.run_telegram_miner_cycle(
        llm=_good_llm(
            durable=True,
            title="Ne jamais réutiliser 31.70.114.74 dans un exemple",
            body="Corps propre, sans secret.",
        ),
        github_client=fake_gh,
    )

    assert result["outcome"] == "blocked_suspected_secret"
    assert fake_gh.calls == []


@pytest.mark.asyncio
async def test_secret_looking_body_blocks_publication(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)
    fake_gh = _FakeGitHubClient()

    result = await tcm.run_telegram_miner_cycle(
        llm=_good_llm(
            durable=True,
            title="Titre propre",
            body="La clé partagée était CG-RgwycjzPV6dPJDE7jDwvuEy1 par erreur.",
        ),
        github_client=fake_gh,
    )

    assert result["outcome"] == "blocked_suspected_secret"
    assert fake_gh.calls == []


def test_looks_like_secret_detects_private_key_header():
    assert tcm._looks_like_secret("-----BEGIN EC PRIVATE KEY-----\nMIGH...") is True


def test_looks_like_secret_false_positive_free_on_normal_prose():
    normal = (
        "L'opérateur préfère toujours confirmer avant un déploiement VPS, "
        "et veut que les décisions financières passent par Telegram."
    )
    assert tcm._looks_like_secret(normal) is False


# ── Watermark : ne remine jamais deux fois les mêmes messages ───────────────

@pytest.mark.asyncio
async def test_watermark_advances_and_second_run_sees_only_new_messages(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)

    seen_prompts: list[str] = []

    async def recording_llm(prompt, system, *, max_tokens=700):
        seen_prompts.append(prompt)
        return json.dumps({"durable": False, "proposal_title": "", "proposal_body": ""})

    first = await tcm.run_telegram_miner_cycle(llm=recording_llm)
    assert first["outcome"] == "not_durable"

    # Un second passage immédiat est throttlé (meme cadence que claude_mentor).
    second = await tcm.run_telegram_miner_cycle(llm=recording_llm)
    assert second["outcome"] == "throttled"
    assert len(seen_prompts) == 1


@pytest.mark.asyncio
async def test_cycle_throttled_after_a_successful_run(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_MINER_ENABLED", "1")
    await _seed_exchange(4)

    first = await tcm.run_telegram_miner_cycle(llm=_good_llm())
    assert first["outcome"] in ("not_durable", "ok")

    second = await tcm.run_telegram_miner_cycle(llm=_good_llm())
    assert second["outcome"] == "throttled"
    assert second["hours_since_last"] < tcm.MIN_INTERVAL_HOURS
