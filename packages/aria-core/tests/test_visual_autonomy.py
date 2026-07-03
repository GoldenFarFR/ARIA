"""Cycle identité visuelle autonome."""

from unittest.mock import AsyncMock

import pytest

from aria_core.testing import AriaRuntimeSettings, configure_test_runtime
from aria_core.visual_autonomy import (
    visual_auto_apply_enabled,
    visual_autonomy_enabled,
)


@pytest.fixture(autouse=True)
def isolated_visual(tmp_path, monkeypatch):
    monkeypatch.delenv("ARIA_VISUAL_AUTONOMY", raising=False)
    monkeypatch.delenv("ARIA_VISUAL_AUTO_APPLY", raising=False)
    avatar = tmp_path / "aria" / "avatar"
    avatar.mkdir(parents=True)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.avatar_identity.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.x_banner.aria_avatar_dir", lambda: avatar)


def test_visual_autonomy_follows_aria_autonomous(monkeypatch):
    from aria_core.runtime import get_settings

    for autonomous in (True, False):
        monkeypatch.delenv("ARIA_VISUAL_AUTONOMY", raising=False)
        configure_test_runtime(settings=AriaRuntimeSettings(aria_autonomous=autonomous))
        setattr(get_settings(), "aria_autonomous", autonomous)
        assert visual_autonomy_enabled() is autonomous


def test_visual_auto_apply_default_true_when_autonomous(monkeypatch):
    configure_test_runtime(
        settings=AriaRuntimeSettings(aria_autonomous=True, aria_visual_auto_apply=True),
    )
    monkeypatch.delenv("ARIA_VISUAL_AUTO_APPLY", raising=False)
    assert visual_auto_apply_enabled() is True


@pytest.mark.asyncio
async def test_run_visual_autonomy_cycle_banner_only(monkeypatch, tmp_path):
    from aria_core.avatar import current_avatar_path, ensure_avatar_seeded
    from aria_core.avatar_identity import establish_identity_anchor
    from aria_core.visual_autonomy import run_visual_autonomy_cycle

    configure_test_runtime(
        settings=AriaRuntimeSettings(
            aria_autonomous=True,
            aria_visual_auto_apply=True,
            aria_banner_auto_refresh=True,
            image_api_key="xai-test",
        ),
    )
    monkeypatch.setenv("ARIA_VISUAL_AUTONOMY", "true")

    ensure_avatar_seeded()
    data = current_avatar_path().read_bytes()

    async def fake_brief(_: bytes) -> str:
        return "ref"

    async def fake_sync() -> dict:
        return {"telegram": True, "x": True, "errors": {}}

    async def fake_verify(_a: bytes, _b: bytes) -> tuple[bool, str]:
        return True, "OUI"

    monkeypatch.setattr("aria_core.avatar_identity._extract_identity_brief", fake_brief)
    monkeypatch.setattr("aria_core.avatar_identity.apply_avatar_sync", fake_sync)
    monkeypatch.setattr("aria_core.avatar_identity._verify_same_person", fake_verify)
    monkeypatch.setattr(
        "aria_core.avatar_style_refresh.run_refresh_cycle",
        AsyncMock(return_value={"skipped": True, "reason": "not_due"}),
    )
    monkeypatch.setattr(
        "aria_core.visual_autonomy.refresh_x_banner_autonomous",
        AsyncMock(return_value={"ok": True, "uploaded": True, "path": "/x"}),
    )
    monkeypatch.setattr(
        "aria_core.visual_autonomy._notify_visual_update",
        AsyncMock(),
    )

    await establish_identity_anchor(data, source="test")
    result = await run_visual_autonomy_cycle(notify=False)
    assert result["ok"] is True
    assert "banner" in result