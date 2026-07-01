import pytest

from aria_core.avatar import (
    apply_avatar_sync,
    aria_choose_avatar,
    current_avatar_path,
    ensure_avatar_seeded,
    get_avatar_status,
    list_gallery,
    pick_gallery_avatar,
)


@pytest.fixture(autouse=True)
def isolated_avatar_dir(tmp_path, monkeypatch):
    avatar = tmp_path / "aria" / "avatar"
    gallery = avatar / "gallery"
    gallery.mkdir(parents=True)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_dir", lambda: avatar)
    monkeypatch.setattr("aria_core.avatar.aria_avatar_gallery_dir", lambda: gallery)


def test_gallery_seeded_with_three_variants():
    ensure_avatar_seeded()
    gallery = list_gallery()
    assert len(gallery) == 3
    assert current_avatar_path().exists()


@pytest.mark.asyncio
async def test_pick_and_choose_avatar(monkeypatch):
    ensure_avatar_seeded()

    async def noop_apply() -> bool:
        return True

    async def noop_sync() -> dict[str, bool]:
        return {"telegram": True, "x": True}

    monkeypatch.setattr("aria_core.avatar.apply_avatar_sync", noop_sync)
    entry = await pick_gallery_avatar("zhc-violet", note="test")
    assert entry["source"] == "gallery:zhc-violet"
    assert entry["sync"] == {"telegram": True, "x": True}
    status = get_avatar_status()
    assert status["has_avatar"] is True

    pick_id = await aria_choose_avatar()
    assert pick_id in {g["id"] for g in list_gallery()}


@pytest.mark.asyncio
async def test_apply_avatar_sync(monkeypatch):
    ensure_avatar_seeded()

    async def fake_tg() -> tuple[bool, str | None]:
        return True, None

    async def fake_x() -> tuple[bool, str | None]:
        return False, "rate limit"

    monkeypatch.setattr("aria_core.avatar.apply_telegram_avatar", fake_tg)
    monkeypatch.setattr("aria_core.avatar.apply_x_avatar", fake_x)
    sync = await apply_avatar_sync()
    assert sync == {
        "telegram": True,
        "x": False,
        "errors": {"x": "rate limit"},
    }