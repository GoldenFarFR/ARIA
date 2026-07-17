"""x_banner.py au-delà de normalize_banner_jpeg (déjà couvert dans
test_x_banner_normalize.py) : gestion fichier local, statut agrégé, lignes HUD."""
from __future__ import annotations

import pytest

from aria_core import x_banner


@pytest.fixture(autouse=True)
def _isolated_avatar_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(x_banner, "aria_avatar_dir", lambda: tmp_path)
    yield


def test_x_banner_path_uses_avatar_dir(tmp_path):
    assert x_banner.x_banner_path() == tmp_path / "x_banner.jpg"


def test_default_banner_scene_has_no_text_overlay_instruction():
    scene = x_banner.default_banner_scene()
    assert "no text overlay" in scene


def test_banner_brand_brief_uses_holding_name(monkeypatch):
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_holding_name", "Aria Vanguard ZHC")
    brief = x_banner._banner_brand_brief()
    assert "Aria Vanguard ZHC" in brief


def test_banner_brand_brief_falls_back_when_holding_name_empty(monkeypatch):
    from aria_core.runtime import settings

    monkeypatch.setattr(settings, "aria_holding_name", "")
    brief = x_banner._banner_brand_brief()
    assert "GoldenFar Vanguard" in brief


@pytest.mark.asyncio
async def test_ensure_x_banner_file_reuses_existing_valid_file(tmp_path, monkeypatch):
    path = tmp_path / "x_banner.jpg"
    path.write_bytes(b"x" * 20_000)  # au-dessus du plancher de 10 000 octets

    async def _should_not_be_called():
        raise AssertionError("ne doit jamais régénérer un fichier déjà valide")

    monkeypatch.setattr(x_banner, "generate_x_banner_jpeg", _should_not_be_called)
    result = await x_banner.ensure_x_banner_file()
    assert result == path


@pytest.mark.asyncio
async def test_ensure_x_banner_file_regenerates_when_too_small(tmp_path, monkeypatch):
    path = tmp_path / "x_banner.jpg"
    path.write_bytes(b"x" * 100)  # sous le plancher -- fichier tronqué/invalide

    async def _fake_generate():
        return b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    monkeypatch.setattr(x_banner, "generate_x_banner_jpeg", _fake_generate)
    monkeypatch.setattr(x_banner, "normalize_banner_jpeg", lambda data: b"normalized")

    result = await x_banner.ensure_x_banner_file()
    assert result == path
    assert path.read_bytes() == b"normalized"


@pytest.mark.asyncio
async def test_ensure_x_banner_file_force_regenerates_even_if_valid(tmp_path, monkeypatch):
    path = tmp_path / "x_banner.jpg"
    path.write_bytes(b"x" * 20_000)

    async def _fake_generate():
        return b"fresh-bytes"

    monkeypatch.setattr(x_banner, "generate_x_banner_jpeg", _fake_generate)
    monkeypatch.setattr(x_banner, "normalize_banner_jpeg", lambda data: b"normalized-fresh")

    result = await x_banner.ensure_x_banner_file(force=True)
    assert result == path
    assert path.read_bytes() == b"normalized-fresh"


@pytest.mark.asyncio
async def test_ensure_x_banner_file_returns_none_when_generation_fails(tmp_path, monkeypatch):
    async def _fake_generate():
        return None

    monkeypatch.setattr(x_banner, "generate_x_banner_jpeg", _fake_generate)
    result = await x_banner.ensure_x_banner_file()
    assert result is None


@pytest.mark.asyncio
async def test_get_x_banner_status_combines_local_and_remote(monkeypatch, tmp_path):
    (tmp_path / "x_banner.jpg").write_bytes(b"local-data")

    async def _fake_remote_status():
        return {"has_banner": True, "banner_url": "https://x.example/banner.jpg"}

    monkeypatch.setattr("aria_core.gateway.x_twitter.get_profile_banner_status", _fake_remote_status)
    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: True)

    status = await x_banner.get_x_banner_status()
    assert status["local_banner"] is True
    assert status["x_configured"] is True
    assert status["has_banner"] is True
    assert status["banner_url"] == "https://x.example/banner.jpg"


@pytest.mark.asyncio
async def test_get_x_banner_status_no_local_file(monkeypatch):
    async def _fake_remote_status():
        return {"has_banner": False}

    monkeypatch.setattr("aria_core.gateway.x_twitter.get_profile_banner_status", _fake_remote_status)
    monkeypatch.setattr("aria_core.gateway.x_twitter.is_x_post_configured", lambda: False)

    status = await x_banner.get_x_banner_status()
    assert status["local_banner"] is False
    assert status["local_path"] is None


@pytest.mark.asyncio
async def test_get_visual_assets_status_aggregates_all_three_assets(monkeypatch, tmp_path):
    (tmp_path / "x_banner.jpg").write_bytes(b"data")

    monkeypatch.setattr("aria_core.avatar.current_avatar_path", lambda: tmp_path / "current.jpg")
    (tmp_path / "current.jpg").write_bytes(b"avatar")
    monkeypatch.setattr("aria_core.avatar_identity.has_identity_anchor", lambda: True)

    async def _fake_status():
        return {"has_banner": True, "x_configured": True, "banner_url": "https://x.example/b.jpg"}

    monkeypatch.setattr(x_banner, "get_x_banner_status", _fake_status)

    status = await x_banner.get_visual_assets_status()
    assert status["avatar_profile"] is True
    assert status["identity_anchor"] is True
    assert status["banner_local"] is True
    assert status["banner_remote"] is True
    assert status["x_configured"] is True
    assert status["banner_url"] == "https://x.example/b.jpg"


def test_format_visual_assets_lines_french(monkeypatch, tmp_path):
    monkeypatch.setattr("aria_core.avatar.current_avatar_path", lambda: tmp_path / "missing.jpg")
    monkeypatch.setattr("aria_core.avatar_identity.has_identity_anchor", lambda: False)

    lines = x_banner.format_visual_assets_lines(lang="fr")
    assert any("Avatar profil" in line for line in lines)
    assert any("non" in line for line in lines)


def test_format_visual_assets_lines_english(monkeypatch, tmp_path):
    monkeypatch.setattr("aria_core.avatar.current_avatar_path", lambda: tmp_path / "missing.jpg")
    monkeypatch.setattr("aria_core.avatar_identity.has_identity_anchor", lambda: True)

    lines = x_banner.format_visual_assets_lines(lang="en")
    assert any("Profile avatar" in line for line in lines)
    assert any("yes" in line for line in lines)
