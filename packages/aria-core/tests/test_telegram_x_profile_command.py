"""/x profile [status|preview|sync|force] — commande admin (tâche #40).

Aucun réseau : aria_core.x_profile est entièrement mocké. Vérifie la restriction
admin, le rendu de preview, l'état "aligné"/"dérive", et les deux variantes
sync/force.
"""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


def _configure_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: uid == 42)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])


@pytest.mark.asyncio
async def test_x_profile_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/x profile sync", user_id=1)
    await telegram_bot._handle_x(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "admin" in update.message.replies[0].lower() or "TELEGRAM_ADMIN_IDS" in update.message.replies[0]


@pytest.mark.asyncio
async def test_x_profile_preview_shows_target_only(monkeypatch):
    _configure_admin(monkeypatch)
    monkeypatch.setattr(
        "aria_core.x_profile.format_profile_summary",
        lambda lang="fr": "Nom : ARIA ZHC\nBio : test\nSite : https://x.example",
    )

    update = FakeUpdate("/x profile preview")
    await telegram_bot._handle_x(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "ARIA ZHC" in update.message.replies[0]


@pytest.mark.asyncio
async def test_x_profile_status_reports_alignment(monkeypatch):
    _configure_admin(monkeypatch)
    target = {"name": "ARIA ZHC", "description": "bio", "url": "https://x.example"}
    monkeypatch.setattr("aria_core.x_profile.canonical_x_profile", lambda: dict(target))
    monkeypatch.setattr("aria_core.x_profile.format_profile_summary", lambda lang="fr": "cible")

    async def _fake_fetch_live():
        return dict(target)

    monkeypatch.setattr("aria_core.x_profile.fetch_live_x_profile", _fake_fetch_live)
    monkeypatch.setattr("aria_core.x_profile.profile_fields_differ", lambda live, tgt: [])

    update = FakeUpdate("/x profile")
    await telegram_bot._handle_x(update, FakeContext())

    reply = update.message.replies[0]
    assert "Aligné sur la narrative Vanguard." in reply


@pytest.mark.asyncio
async def test_x_profile_status_reports_drift_and_suggests_sync(monkeypatch):
    _configure_admin(monkeypatch)
    target = {"name": "ARIA ZHC", "description": "bio", "url": "https://x.example"}
    monkeypatch.setattr("aria_core.x_profile.canonical_x_profile", lambda: dict(target))
    monkeypatch.setattr("aria_core.x_profile.format_profile_summary", lambda lang="fr": "cible")

    async def _fake_fetch_live():
        return {"name": "Old Name", "description": "bio", "url": "https://x.example"}

    monkeypatch.setattr("aria_core.x_profile.fetch_live_x_profile", _fake_fetch_live)
    monkeypatch.setattr("aria_core.x_profile.profile_fields_differ", lambda live, tgt: ["name"])

    update = FakeUpdate("/x profile")
    await telegram_bot._handle_x(update, FakeContext())

    reply = update.message.replies[0]
    assert "Dérive : name" in reply
    assert "/x profile sync" in reply


@pytest.mark.asyncio
async def test_x_profile_sync_reports_synced_fields(monkeypatch):
    _configure_admin(monkeypatch)

    async def _fake_sync(*, force=False):
        assert force is False
        return {"synced": True, "drift": ["name", "description"]}

    monkeypatch.setattr("aria_core.x_profile.sync_x_profile", _fake_sync)

    update = FakeUpdate("/x profile sync")
    await telegram_bot._handle_x(update, FakeContext())

    reply = update.message.replies[0]
    assert "synchronisé" in reply
    assert "name, description" in reply


@pytest.mark.asyncio
async def test_x_profile_force_bypasses_drift_check(monkeypatch):
    _configure_admin(monkeypatch)

    async def _fake_sync(*, force=False):
        assert force is True
        return {"synced": True, "drift": []}

    monkeypatch.setattr("aria_core.x_profile.sync_x_profile", _fake_sync)

    update = FakeUpdate("/x profile force")
    await telegram_bot._handle_x(update, FakeContext())

    reply = update.message.replies[0]
    assert "synchronisé" in reply


@pytest.mark.asyncio
async def test_x_profile_sync_reports_skip_reason(monkeypatch):
    _configure_admin(monkeypatch)

    async def _fake_sync(*, force=False):
        return {"synced": False, "skipped": True, "reason": "x_not_configured"}

    monkeypatch.setattr("aria_core.x_profile.sync_x_profile", _fake_sync)
    monkeypatch.setattr("aria_core.x_profile.format_profile_summary", lambda lang="fr": "cible")

    update = FakeUpdate("/x profile sync")
    await telegram_bot._handle_x(update, FakeContext())

    reply = update.message.replies[0]
    assert "rien à faire" in reply
    # 18/07 -- _format_tg ne strippe plus les underscores internes à un identifiant
    # (cf. test_telegram_format.py::test_plain_telegram_preserves_snake_case_identifiers).
    assert "x_not_configured" in reply
