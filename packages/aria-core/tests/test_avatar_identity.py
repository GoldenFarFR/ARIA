import pytest

from aria_core.avatar import current_avatar_path, ensure_avatar_seeded
from aria_core.avatar_identity import (
    ensure_identity_anchor_from_current,
    establish_identity_anchor,
    get_identity_status,
    has_identity_anchor,
    identity_anchor_path,
    reset_identity_anchor,
    set_pending_identity_anchor,
    set_profile_with_identity,
)


@pytest.fixture(autouse=True)
def isolated_avatar_dir(tmp_path, monkeypatch):
    avatar = tmp_path / "aria" / "avatar"
    gallery = avatar / "gallery"
    gallery.mkdir(parents=True)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_gallery_dir", lambda: gallery)
    monkeypatch.setattr("aria_core.avatar_identity.aria_avatar_dir", lambda: avatar)


@pytest.mark.asyncio
async def test_establish_identity_anchor(monkeypatch):
    ensure_avatar_seeded()
    data = current_avatar_path().read_bytes()

    async def fake_brief(_: bytes) -> str:
        return "Femme professionnelle, cheveux bruns"

    async def fake_sync() -> dict:
        return {"telegram": True, "x": True, "errors": {}}

    monkeypatch.setattr("aria_core.avatar_identity._extract_identity_brief", fake_brief)
    monkeypatch.setattr("aria_core.avatar_identity.apply_avatar_sync", fake_sync)

    entry = await establish_identity_anchor(data, source="test", note="ref")
    assert entry["identity"]["established"] is True
    assert has_identity_anchor()
    st = get_identity_status()
    assert st["locked"] is True
    assert "bruns" in st["brief"]


@pytest.mark.asyncio
async def test_reject_different_person(monkeypatch):
    ensure_avatar_seeded()
    data = current_avatar_path().read_bytes()

    async def fake_brief(_: bytes) -> str:
        return "ref"

    async def fake_sync() -> dict:
        return {"telegram": True, "x": True, "errors": {}}

    async def fake_verify(_a: bytes, _b: bytes) -> tuple[bool, str]:
        return False, "NON personne différente"

    monkeypatch.setattr("aria_core.avatar_identity._extract_identity_brief", fake_brief)
    monkeypatch.setattr("aria_core.avatar_identity.apply_avatar_sync", fake_sync)
    monkeypatch.setattr("aria_core.avatar_identity._verify_same_person", fake_verify)

    await establish_identity_anchor(data, source="test")
    with pytest.raises(ValueError, match="identité ARIA"):
        await set_profile_with_identity(data, source="test2")


def test_ensure_identity_anchor_from_current():
    ensure_avatar_seeded()
    reset_identity_anchor()
    assert not has_identity_anchor()
    assert ensure_identity_anchor_from_current() is True
    assert has_identity_anchor()


def test_reset_identity():
    ensure_avatar_seeded()
    reset_identity_anchor()
    set_pending_identity_anchor(True)
    st = get_identity_status()
    assert st["pending_anchor"] is True
    assert not identity_anchor_path().exists()