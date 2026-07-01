import pytest

from aria_core.knowledge.web_verify import WebSource
from aria_core.presentation import format_live_info_brief, format_live_info_response


def test_format_live_info_picks_time_snippet():
    snippets = [
        "Calendrier Top 14 disponible sur LNR.",
        "Demi-finale vendredi 19 juin 2026 à 21h05 au Vélodrome.",
    ]
    body = format_live_info_brief(snippets, lang="fr", query="heure match toulousain")
    assert "21h05" in body
    assert "ACTU" in body
    assert "📎 Sources" in body


def test_format_live_info_bitcoin_direction():
    sources = [
        WebSource(
            text="Bitcoin chute de 4,26 % à 62 671 $ le 18 juin 2026.",
            url="https://example.com/btc",
        ),
        WebSource(
            text="Le BTC poursuit sa correction sous 63 000 $.",
            url="https://example.com/btc2",
        ),
    ]
    body = format_live_info_response(
        None, sources, lang="fr", query="le prix du bitcoin baisse ou monte ?", fallback=True,
    )
    assert "baisse" in body.lower()
    assert "62" in body or "63" in body
    assert "📎 https://example.com/btc" in body
    assert body.index("baisse") < body.index("📎 Sources")


def test_format_live_info_direct_answer_first():
    body = format_live_info_response(
        "Le Bitcoin est en baisse aujourd'hui.",
        [WebSource(text="snippet", url="https://a.com")],
        lang="fr",
        query="bitcoin",
        fallback=False,
    )
    assert body.split("📎 Sources")[0].strip().endswith("aujourd'hui.")


@pytest.mark.asyncio
async def test_web_enhance_snippet_fallback_when_groq_fails(monkeypatch):
    from aria_core.knowledge import web_verify as wv

    async def fake_snippets(_q):
        return [WebSource(text="Stade Toulousain joue à 21h05 ce vendredi.", url="https://lnr.fr")]

    async def fake_chat(*_a, **_k):
        return "invalid"

    monkeypatch.setattr(wv, "fetch_web_snippets", fake_snippets)
    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)

    reply, meta = await wv.web_enhance_calibrated(
        "heure match toulousain",
        None,
        {"p_true": 0.3, "truth": "INCERTAIN"},
        "fr",
        force=True,
    )
    assert reply
    assert "21h05" in reply
    assert "📎 https://lnr.fr" in reply
    assert meta.get("web_verify") == "snippets_fallback"