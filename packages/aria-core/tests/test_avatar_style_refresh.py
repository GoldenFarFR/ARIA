import pytest

from aria_core.avatar import current_avatar_path, ensure_avatar_seeded
from aria_core.avatar_identity import establish_identity_anchor
from aria_core.avatar_style_refresh import (
    _compute_next_due,
    apply_pending_style,
    bootstrap_style_schedule,
    discard_pending,
    generate_pending_style,
    get_refresh_status,
    is_due,
    update_config,
)


@pytest.fixture(autouse=True)
def isolated_avatar_style(tmp_path, monkeypatch):
    avatar = tmp_path / "aria" / "avatar"
    gallery = avatar / "gallery"
    gallery.mkdir(parents=True)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_gallery_dir", lambda: gallery)
    monkeypatch.setattr("aria_core.avatar_identity.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.avatar_style_refresh.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr(
        "aria_core.avatar_style_refresh._image_api_key",
        lambda: "test-key",
    )


@pytest.mark.asyncio
async def test_generate_and_apply_pending(monkeypatch):
    ensure_avatar_seeded()
    data = current_avatar_path().read_bytes()

    async def fake_brief(_: bytes) -> str:
        return "ref visage"

    async def fake_sync() -> dict:
        return {"telegram": True, "x": True, "errors": {}}

    async def fake_verify(_a: bytes, _b: bytes) -> tuple[bool, str]:
        return True, "OUI"

    async def fake_style(*_a, **_k) -> bytes:
        return data

    async def fake_propose(**_k) -> str:
        return "Lumière dorée ZHC, fond minimal."

    monkeypatch.setattr("aria_core.avatar_identity._extract_identity_brief", fake_brief)
    monkeypatch.setattr("aria_core.avatar_identity.apply_avatar_sync", fake_sync)
    monkeypatch.setattr("aria_core.avatar_identity._verify_same_person", fake_verify)
    monkeypatch.setattr(
        "aria_core.avatar_style_refresh.generate_style_from_anchor_file",
        fake_style,
    )
    monkeypatch.setattr("aria_core.avatar_style_refresh.propose_style", fake_propose)

    await establish_identity_anchor(data, source="test")
    result = await generate_pending_style()
    assert result["ok"] is True
    assert "dorée" in result["pending"]["style_prompt"]

    applied = await apply_pending_style()
    assert applied["ok"] is True
    st = get_refresh_status()
    assert st["has_pending"] is False
    assert st["last_run_at"]
    assert st["next_due_at"]


def test_interval_config_and_due():
    st = update_config(enabled=True, interval_days=14)
    assert st["interval_days"] == 14
    with pytest.raises(ValueError):
        update_config(interval_days=10)

    boot = bootstrap_style_schedule()
    assert boot["action"] in ("initialized", "from_last_run", "from_history", "unchanged")
    assert is_due() is False
    discard_pending()


@pytest.mark.asyncio
async def test_discard_pending(monkeypatch):
    ensure_avatar_seeded()
    data = current_avatar_path().read_bytes()

    async def fake_brief(_: bytes) -> str:
        return "ref"

    async def fake_sync() -> dict:
        return {"telegram": True, "x": True, "errors": {}}

    async def fake_style(*_a, **_k) -> bytes:
        return data

    async def fake_propose(**_k) -> str:
        return "Style test"

    monkeypatch.setattr("aria_core.avatar_identity._extract_identity_brief", fake_brief)
    monkeypatch.setattr("aria_core.avatar_identity.apply_avatar_sync", fake_sync)
    monkeypatch.setattr(
        "aria_core.avatar_style_refresh.generate_style_from_anchor_file",
        fake_style,
    )
    monkeypatch.setattr("aria_core.avatar_style_refresh.propose_style", fake_propose)

    await establish_identity_anchor(data, source="test")
    await generate_pending_style()
    assert get_refresh_status()["has_pending"] is True
    discard_pending()
    assert get_refresh_status()["has_pending"] is False


def test_compute_next_due_days(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.delenv("ARIA_AVATAR_STYLE_INTERVAL_DAYS", raising=False)
    base = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    update_config(interval_days=14)
    nxt = _compute_next_due(base)
    assert "2026-06-15" in nxt