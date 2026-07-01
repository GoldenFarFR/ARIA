import pytest

from aria_core.knowledge.seed import _zhc_identity_topics, seed_zhc_identity_knowledge


def test_zhc_identity_topics_cover_role():
    topics = dict(_zhc_identity_topics())
    assert "zhc-identity" in topics
    assert "zhc-model" in topics
    assert "zhc-role-cao" in topics
    assert "ZHC" in topics["zhc-identity"]
    assert "CAO duties" in topics["zhc-role-cao"]


@pytest.mark.asyncio
async def test_seed_zhc_identity_upserts(tmp_path):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    configure_test_runtime(data_dir=tmp_path, settings=AriaRuntimeSettings())

    n = await seed_zhc_identity_knowledge()
    assert n == len(_zhc_identity_topics())

    from aria_core.knowledge.cognitive import get_approved

    items = await get_approved(limit=30)
    zhc = [i for i in items if i.topic.startswith("zhc-")]
    assert len(zhc) >= 6
    assert all(i.approved for i in zhc)