"""Profil public X @Aria_ZHC -- sync nom/bio/site (seam livré, tâche #40).

Toutes les fonctions bas niveau de gateway/x_twitter.py sont mockées : aucun
appel réseau réel.
"""
from __future__ import annotations

import pytest

from aria_core import x_profile


def test_canonical_x_profile_uses_narrative_and_identity():
    from aria_core.identity import ARIA_DISPLAY_NAME
    from aria_core.narrative import holding_site_url, x_bio

    target = x_profile.canonical_x_profile()
    assert target["name"] == ARIA_DISPLAY_NAME
    assert target["description"] == x_bio()
    assert target["url"] == holding_site_url()
    assert "location" not in target  # jamais un champ inventé sans source canonique


def test_format_profile_summary_fr_and_en():
    fr = x_profile.format_profile_summary(lang="fr")
    en = x_profile.format_profile_summary(lang="en")
    assert "Nom :" in fr and "Bio :" in fr and "Site :" in fr
    assert "Name:" in en and "Bio:" in en and "URL:" in en


def test_profile_fields_differ_detects_drift():
    live = {"name": "Old Name", "description": "old bio", "url": "https://old.example"}
    target = {"name": "New Name", "description": "old bio", "url": "https://new.example"}
    drift = x_profile.profile_fields_differ(live, target)
    assert set(drift) == {"name", "url"}


def test_profile_fields_differ_empty_when_equal():
    same = {"name": "X", "description": "Y", "url": "Z"}
    assert x_profile.profile_fields_differ(same, dict(same)) == []


@pytest.mark.asyncio
async def test_sync_x_profile_skips_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: False
    )
    result = await x_profile.sync_x_profile()
    assert result == {"synced": False, "skipped": True, "reason": "x_not_configured"}


@pytest.mark.asyncio
async def test_sync_x_profile_noop_when_no_drift(monkeypatch):
    target = x_profile.canonical_x_profile()
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )

    async def _fake_fetch():
        return dict(target)

    applied = {"called": False}

    async def _fake_apply(profile):
        applied["called"] = True
        return True

    monkeypatch.setattr("aria_core.gateway.x_twitter.fetch_x_profile_fields", _fake_fetch)
    monkeypatch.setattr("aria_core.gateway.x_twitter.apply_x_profile_fields", _fake_apply)

    result = await x_profile.sync_x_profile()
    assert result == {"synced": True, "drift": []}
    assert applied["called"] is False  # aucun appel écriture inutile


@pytest.mark.asyncio
async def test_sync_x_profile_applies_when_drift(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )

    async def _fake_fetch():
        return {"name": "Stale Name", "description": "stale bio", "url": "https://stale.example"}

    async def _fake_apply(profile):
        return True

    monkeypatch.setattr("aria_core.gateway.x_twitter.fetch_x_profile_fields", _fake_fetch)
    monkeypatch.setattr("aria_core.gateway.x_twitter.apply_x_profile_fields", _fake_apply)

    result = await x_profile.sync_x_profile()
    assert result["synced"] is True
    assert set(result["drift"]) == {"name", "description", "url"}


@pytest.mark.asyncio
async def test_sync_x_profile_force_applies_even_without_drift(monkeypatch):
    target = x_profile.canonical_x_profile()
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )

    async def _fake_fetch():
        return dict(target)

    applied = {"called": False}

    async def _fake_apply(profile):
        applied["called"] = True
        return True

    monkeypatch.setattr("aria_core.gateway.x_twitter.fetch_x_profile_fields", _fake_fetch)
    monkeypatch.setattr("aria_core.gateway.x_twitter.apply_x_profile_fields", _fake_apply)

    result = await x_profile.sync_x_profile(force=True)
    assert result == {"synced": True, "drift": []}
    assert applied["called"] is True


@pytest.mark.asyncio
async def test_sync_x_profile_reports_error_when_apply_fails(monkeypatch):
    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )

    async def _fake_fetch():
        return {"name": "Stale", "description": "stale", "url": "https://stale.example"}

    async def _fake_apply(profile):
        return False  # ex: outgoing_pause actif, cf. test_outgoing_pause.py

    monkeypatch.setattr("aria_core.gateway.x_twitter.fetch_x_profile_fields", _fake_fetch)
    monkeypatch.setattr("aria_core.gateway.x_twitter.apply_x_profile_fields", _fake_apply)

    result = await x_profile.sync_x_profile()
    assert result["synced"] is False
    assert result["error"] == "x_api_call_failed"
    assert result["drift"]


def test_x_profile_sync_enabled_env_gate(monkeypatch):
    monkeypatch.delenv("ARIA_X_PROFILE_SYNC_ENABLED", raising=False)
    assert x_profile.x_profile_sync_enabled() is False
    monkeypatch.setenv("ARIA_X_PROFILE_SYNC_ENABLED", "true")
    assert x_profile.x_profile_sync_enabled() is True


def test_heartbeat_x_profile_sync_task_gated_off_by_default(monkeypatch):
    from aria_core import heartbeat as hb

    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )
    monkeypatch.delenv("ARIA_X_PROFILE_SYNC_ENABLED", raising=False)
    hb._sync_x_curiosity_enabled()
    task = next(t for t in hb.HEARTBEAT_TASKS if t.id == "x_profile_sync")
    assert task.enabled is False  # outward-facing autonome -> opt-in requis


def test_heartbeat_x_profile_sync_task_enabled_when_flag_set(monkeypatch):
    from aria_core import heartbeat as hb

    monkeypatch.setattr(
        "aria_core.gateway.x_twitter.is_x_post_configured", lambda: True
    )
    monkeypatch.setenv("ARIA_X_PROFILE_SYNC_ENABLED", "true")
    hb._sync_x_curiosity_enabled()
    task = next(t for t in hb.HEARTBEAT_TASKS if t.id == "x_profile_sync")
    assert task.enabled is True
