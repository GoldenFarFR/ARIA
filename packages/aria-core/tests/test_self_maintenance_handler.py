import pytest

from aria_core.operator_self_directive import OperatorMessageKind, classify_operator_message


@pytest.mark.asyncio
async def test_handle_operator_banner_not_actu(monkeypatch):
    """Ordre banniere ne doit pas tomber dans ACTU."""
    from aria_core.self_maintenance import handle_operator_self_message

    called = {"cycle": False}

    async def fake_cycle(*, lang="fr"):
        called["cycle"] = True
        return "Banniere OK — action faite"

    monkeypatch.setattr(
        "aria_core.self_maintenance.run_curiosity_x_banner_cycle",
        fake_cycle,
    )

    msg = "Tu va mettre a jour ta banniere sur X ?"
    assert classify_operator_message(msg) == OperatorMessageKind.SELF_DIRECTIVE
    out = await handle_operator_self_message(msg, lang="fr")
    assert out == "Banniere OK — action faite"
    assert called["cycle"]


@pytest.mark.asyncio
async def test_curiosity_banner_blocked_without_image_key(monkeypatch):
    from aria_core.self_maintenance import run_curiosity_x_banner_cycle

    async def fake_status():
        return {"has_banner": False, "local_banner": False, "x_configured": True}

    monkeypatch.setattr("aria_core.x_banner.get_x_banner_status", fake_status)
    monkeypatch.setattr("aria_core.avatar_identity.has_identity_anchor", lambda: True)
    monkeypatch.setattr("aria_core.portrait_scene._image_api_key", lambda: "")

    async def fake_gap(cap_id, *, context="", lang="fr"):
        return {"status": "local_only", "capability_id": cap_id, "issue_url": ""}

    monkeypatch.setattr("aria_core.capability_gap.file_capability_gap", fake_gap)
    monkeypatch.setattr("aria_core.memory.append_memory", lambda *a, **k: None)

    out = await run_curiosity_x_banner_cycle(lang="fr")
    assert "IMAGE_API_KEY" in out
    assert "Action bloquee" in out


@pytest.mark.asyncio
async def test_handle_general_info_returns_none():
    from aria_core.self_maintenance import handle_operator_self_message

    out = await handle_operator_self_message("Comment utiliser Twitter ?", lang="fr")
    assert out is None