"""Pipeline de sorties : munitions marketing + diffusion X/TikTok synchronisée au site."""
from __future__ import annotations

import aria_core.release_pipeline as rp
import pytest


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "DB_PATH", str(tmp_path / "rel.db"))


@pytest.mark.asyncio
async def test_manifest_loads_and_all_built_initially():
    rels = await rp.list_releases()
    assert len(rels) >= 10
    assert all(r.status == "built" for r in rels)
    # contenu prêt à diffuser + affichage site
    assert all(r.pitch and r.blurb for r in rels)


@pytest.mark.asyncio
async def test_public_view_hides_internal_pitch():
    pub = await rp.public_releases()
    assert pub and "pitch" not in pub[0]
    assert {"id", "title", "status", "blurb"} <= set(pub[0])


@pytest.mark.asyncio
async def test_set_status_persists_and_unknown_rejected():
    rid = (await rp.list_releases())[0].id
    assert await rp.set_status(rid, "live") is True
    rels = {r.id: r for r in await rp.list_releases()}
    assert rels[rid].status == "live"
    assert await rp.set_status("does-not-exist", "live") is False
    with pytest.raises(ValueError):
        await rp.set_status(rid, "banana")


@pytest.mark.asyncio
async def test_announce_next_advances_in_order():
    first = await rp.next_to_announce()
    out = await rp.announce_next()
    assert out["id"] == first.id
    # la suivante n'est plus la même
    assert (await rp.next_to_announce()).id != first.id


@pytest.mark.asyncio
async def test_campaign_gated_by_default():
    # Sans feu vert opérateur : rien ne sort, aucun teaser.
    assert await rp.is_campaign_armed() is False
    res = await rp.publish_release(x_publisher=lambda *a: True)
    assert res.get("blocked")
    assert await rp.next_teaser() is None


@pytest.mark.asyncio
async def test_teasers_available_but_gated():
    assert len(rp.list_teasers()) >= 5           # contenu prêt
    assert await rp.next_teaser() is None          # mais muet tant que non armé
    await rp.arm_campaign()
    assert await rp.next_teaser() is not None       # armé -> diffusable


@pytest.mark.asyncio
async def test_publish_dispatches_channels_and_syncs_site():
    await rp.arm_campaign()  # feu vert opérateur
    calls = []

    async def x_pub(text, rel):
        calls.append(("x", rel.id, text))
        return True

    async def tt_pub(text, rel):
        calls.append(("tiktok", rel.id, text))
        return True

    res = await rp.publish_release(x_publisher=x_pub, tiktok_publisher=tt_pub)
    assert set(res["published_to"]) == {"x", "tiktok"}
    assert res["status"] == "live"            # site synchronisé (live)
    assert res["link"].endswith(res["id"])    # lien vers la feature sur le site
    # les deux canaux ont reçu le pitch + le lien
    assert any(c[0] == "x" for c in calls) and any(c[0] == "tiktok" for c in calls)
    # le statut est persisté -> le site le lira comme 'live'
    rels = {r.id: r for r in await rp.list_releases()}
    assert rels[res["id"]].status == "live"


@pytest.mark.asyncio
async def test_publish_tiktok_seam_pending_when_not_configured():
    await rp.arm_campaign()

    async def x_pub(text, rel):
        return True

    res = await rp.publish_release(x_publisher=x_pub)  # pas de TikTok
    assert "x" in res["published_to"]
    assert "tiktok" in res["pending_channels"]  # seam posé, non bloquant


@pytest.mark.asyncio
async def test_publish_one_channel_failure_not_fatal():
    await rp.arm_campaign()

    async def x_pub(text, rel):
        raise RuntimeError("x down")

    async def tt_pub(text, rel):
        return True

    res = await rp.publish_release(x_publisher=x_pub, tiktok_publisher=tt_pub)
    assert res["published_to"] == ["tiktok"]
    assert "x" in res["pending_channels"]
    assert res["status"] == "live"  # le site se synchronise quand même


@pytest.mark.asyncio
async def test_publish_explicit_false_lands_in_pending_not_silently_dropped():
    """#127 (trouvé par Principal en livrant #34) : un publisher injecté qui renvoie
    False SANS lever une exception disparaissait des DEUX listes (published_to ET
    pending_channels) -- silencieux, aucune trace. Un False explicite doit avoir le
    même sort qu'un canal sans publisher configuré : toujours dans pending_channels."""
    await rp.arm_campaign()

    async def x_pub(text, rel):
        return True

    async def tt_pub(text, rel):
        return False  # échec explicite, pas d'exception

    res = await rp.publish_release(x_publisher=x_pub, tiktok_publisher=tt_pub)
    assert res["published_to"] == ["x"]
    assert "tiktok" in res["pending_channels"]
    assert "tiktok" not in res["published_to"]


@pytest.mark.asyncio
async def test_publish_returns_none_when_exhausted():
    await rp.arm_campaign()
    # tout passer en live -> plus de 'built'
    for r in await rp.list_releases():
        await rp.set_status(r.id, "live")
    assert await rp.announce_next() is None
    assert await rp.publish_release() is None
