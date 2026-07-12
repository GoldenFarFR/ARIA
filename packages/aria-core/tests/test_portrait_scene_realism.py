"""portrait_scene — prompts de génération de portrait renforcés (réalisme + marque),
avec la frontière "jamais sexualisé" gravée dans le prompt lui-même (décision opérateur
explicite du 10/07, AskUserQuestion : niveau de réalisme Lily Turner SANS son registre)."""
from __future__ import annotations

import pytest

from aria_core import portrait_scene


@pytest.mark.asyncio
async def test_generate_scene_portrait_includes_realism_and_boundary(monkeypatch):
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    result = await portrait_scene.generate_scene_portrait(
        b"anchor", identity_brief="pink hair, freckles, confident", scene="rooftop at sunset"
    )

    assert result == b"fake-image"
    prompt = captured["prompt"]
    assert "unmistakably recognizable" in prompt
    assert "85mm portrait lens" in prompt
    assert "natural skin texture" in prompt
    assert "never sexualized" in prompt
    assert "never suggestive" in prompt
    assert "rooftop at sunset" in prompt


@pytest.mark.asyncio
async def test_generate_style_portrait_includes_realism_and_boundary(monkeypatch):
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    await portrait_scene.generate_style_portrait(
        b"anchor", identity_brief="pink hair", style="editorial fashion shoot"
    )

    prompt = captured["prompt"]
    assert "never sexualized" in prompt
    assert "commanding presence" in prompt
    assert "editorial fashion shoot" in prompt


@pytest.mark.asyncio
async def test_generate_banner_portrait_includes_realism_and_boundary(monkeypatch):
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    await portrait_scene.generate_banner_portrait(
        b"anchor", identity_brief="pink hair", scene="trading floor"
    )

    prompt = captured["prompt"]
    assert "never sexualized" in prompt
    assert "unmistakably recognizable" in prompt


def test_identity_brief_truncation_extended_not_120():
    # Le brief n'est plus tronqué à 120 -- 220 caractères conservés, plus de fidélité.
    long_brief = "x" * 500
    assert portrait_scene._IDENTITY_BRIEF_MAX == 220
    assert len(long_brief[: portrait_scene._IDENTITY_BRIEF_MAX]) == 220


@pytest.mark.asyncio
async def test_generate_scene_portrait_locks_brand_palette_no_violet(monkeypatch):
    """#94 -- la palette de marque (or/charbon, jamais violet/néon) est gravée dans le
    prompt PARTAGÉ, pas laissée à la seule discipline des presets appelants."""
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    await portrait_scene.generate_scene_portrait(
        b"anchor", identity_brief="confident", scene="rooftop at sunset"
    )
    prompt = captured["prompt"].lower()
    assert "gold" in prompt and "charcoal" in prompt
    assert "never neon" in prompt
    assert "violet or purple" in prompt
    assert "generic ai-generated" in prompt


@pytest.mark.asyncio
async def test_generate_style_portrait_locks_brand_palette_no_violet(monkeypatch):
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    await portrait_scene.generate_style_portrait(
        b"anchor", identity_brief="confident", style="editorial fashion shoot"
    )
    prompt = captured["prompt"].lower()
    assert "violet or purple" in prompt
    assert "editorial fashion shoot" in prompt


@pytest.mark.asyncio
async def test_generate_banner_portrait_locks_brand_palette_no_violet(monkeypatch):
    captured = {}

    async def fake_edit(*, prompt, anchor_jpeg):
        captured["prompt"] = prompt
        return b"fake-image"

    monkeypatch.setattr(portrait_scene, "_call_image_edit", fake_edit)

    await portrait_scene.generate_banner_portrait(
        b"anchor", identity_brief="confident", scene="trading floor"
    )
    prompt = captured["prompt"].lower()
    assert "violet or purple" in prompt


@pytest.mark.asyncio
async def test_generate_banner_creative_still_excludes_people(monkeypatch):
    # Le banner text-to-image (sans photo source) exclut déjà tout humain -- la
    # frontière de goût n'a pas de sens ici (rien à sexualiser), comportement inchangé.
    captured = {}

    async def fake_generate(*, prompt, aspect_ratio="2:1"):
        captured["prompt"] = prompt
        return b"fake-banner"

    monkeypatch.setattr(portrait_scene, "_call_image_generate", fake_generate)

    await portrait_scene.generate_banner_creative(brand_brief="ARIA holding")
    assert "no people" in captured["prompt"]
    assert "no woman" in captured["prompt"]
