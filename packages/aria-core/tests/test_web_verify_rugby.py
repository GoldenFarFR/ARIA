import pytest

from aria_core.knowledge.web_verify import (
    WebSource,
    _parse_ddg_html,
    _query_variants,
    fetch_web_snippets,
    is_live_info_question,
)


def test_is_live_info_rugby():
    assert is_live_info_question("à quelle heure joue le stade toulousain aujourd'hui ?")


def test_query_variants_rugby():
    variants = _query_variants("à quelle heure joue le stade toulousain aujourd'hui ?")
    assert any("Stade Toulousain" in v or "Top 14" in v for v in variants)


def test_parse_ddg_html_snippets():
    html = (
        '<div class="result__body">'
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Flnr.fr">LNR</a>'
        '<a class="result__snippet">'
        "Demi-finale Top 14 vendredi 19 juin 2026 à 21h05 au Vélodrome.</a>"
        "</div></div>"
    )
    sn = _parse_ddg_html(html)
    assert sn and "21h05" in sn[0].text
    assert sn[0].url == "https://lnr.fr"


def test_is_live_info_bitcoin():
    assert is_live_info_question("le prix du bitcoin baisse ou monte ?")


@pytest.mark.asyncio
async def test_fetch_web_snippets_rugby_live():
    snippets = await fetch_web_snippets("à quelle heure joue le stade toulousain aujourd'hui ?")
    assert snippets
    assert all(isinstance(s, WebSource) for s in snippets)
    joined = " ".join(s.text for s in snippets).lower()
    assert "toulousain" in joined or "top 14" in joined