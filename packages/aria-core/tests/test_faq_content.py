from aria_core.content.service import list_faq, search_faq
from aria_core.skills.faq_skill import execute_faq_lookup


def test_list_faq_has_entries():
    items = list_faq()
    assert len(items) >= 5
    assert items[0]["question"]


def test_search_faq_dexpulse_retired():
    matches = search_faq("What is DEXPulse")
    assert matches
    hit = matches[0]
    assert hit.get("id") in ("dexpulse-product", "dexpulse-retired")
    assert "RETIRED" in hit.get("answer", "").upper() or "retiré" in hit.get("answer", "").lower()


async def test_faq_skill_returns_answer():
    text, data = await execute_faq_lookup("What is ARIA?")
    assert "ARIA" in text
    assert data["count"] >= 1