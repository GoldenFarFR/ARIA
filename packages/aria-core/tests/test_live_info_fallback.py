import pytest

from aria_core.knowledge.web_verify import (
    _WEB_RECAL_PROMPT_EN,
    _WEB_RECAL_PROMPT_FR,
    WebSource,
)
from aria_core.presentation import format_live_info_brief, format_live_info_response


def test_web_recal_prompt_forbids_cross_competition_conflation():
    # Incident reel (10/07) : ARIA a affirme "France vs Espagne en demi-finale de la
    # Coupe du monde" en citant une source qui parlait en fait de la Ligue des Nations
    # (competition differente), et un quart de finale meme pas encore joue. Le prompt
    # devait forcer une verification "meme evenement" + INCERTAIN si le resultat futur
    # n'est pas encore determine, au lieu d'inventer un adversaire plausible.
    for tpl in (_WEB_RECAL_PROMPT_FR, _WEB_RECAL_PROMPT_EN):
        low = tpl.lower()
        assert "même compétition" in low or "same competition" in low
        assert "incertain" in low


def test_web_recal_prompt_forbids_third_party_opinion_attribution():
    # Incident reel (11/07) : "tu es maxi btc ou maxi eth ?" a fait repondre "Je suis
    # maxi eth" en citant un article CNBC sur l'opinion de Mark Cuban ("Ethereum
    # maximalist") comme source "verified web sources" -- l'article ne parle meme pas
    # d'ARIA (contrairement a l'incident rugby du 10/07 ou la source parlait au moins
    # du bon sport). La regle "meme competition/evenement" ne generalise pas a ce cas :
    # il fallait une regle distincte exigeant la MEME ENTITE que celle interrogee, pas
    # seulement un theme proche, avant d'attribuer une affirmation comme un fait ARIA.
    for tpl in (_WEB_RECAL_PROMPT_FR, _WEB_RECAL_PROMPT_EN):
        low = tpl.lower()
        assert "entité" in low or "entity" in low
        assert "aria" in low
        assert "maximalist" in low  # couvre "maximaliste" (FR) et "maximalist" (EN)


@pytest.mark.asyncio
async def test_web_enhance_surfaces_honest_decline_without_forcing_attribution(monkeypatch):
    # Meme si le LLM (mocke ici) suit desormais la regle du prompt et refuse d'attribuer
    # l'opinion de Mark Cuban a ARIA (FAIT: INCERTAIN, reponse honnete), le pipeline ne
    # doit RIEN transformer en fait verifie force -- la simple presence de sources web ne
    # doit jamais fabriquer une position ARIA qui n'existe pas.
    from aria_core.knowledge import web_verify as wv

    async def fake_snippets(_q):
        return [
            WebSource(
                text="Mark Cuban says he is an Ethereum maximalist, not Bitcoin.",
                url="https://cnbc.com/mark-cuban-eth",
            )
        ]

    async def fake_chat(*_a, **_k):
        return (
            "FAIT: INCERTAIN\n"
            "REPONSE: Aucune source ne parle de la position d'ARIA elle-même sur "
            "BTC/ETH, seulement de celle de Mark Cuban -- je n'ai pas de doctrine "
            "maximaliste établie.\n"
            "P_VRAI: 0.20\n"
            "P_FAUX: 0.10\n"
            "RAISON: source parle de Mark Cuban, pas d'ARIA"
        )

    monkeypatch.setattr(wv, "fetch_web_snippets", fake_snippets)
    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)

    reply, meta = await wv.web_enhance_calibrated(
        "tu es maxi btc ou maxi eth ?",
        None,
        {"p_true": 0.3, "truth": "INCERTAIN"},
        "fr",
        force=True,
    )
    assert reply
    assert "maxi eth" not in reply.lower()
    assert "maxi btc" not in reply.lower()
    assert "mark cuban" in reply.lower()  # source citée honnêtement, pas cachée
    assert meta.get("truth") == "incertain"


def test_web_recal_prompt_forbids_generic_fabrication_beyond_aria_facts():
    # Incident reel (14/07) : "quelles sont les dernieres news crypto importantes ?" a
    # produit une reponse citant des faits precis (depart d'une dirigeante d'OpenAI,
    # retraits massifs Binance lies a MiCA) alors qu'AUCUNE des sources affichees
    # (extraits Tavily generiques type "les dernieres news crypto") ne mentionnait ces
    # faits. La regle "n'invente pas de faits ARIA/GoldenFar" ne couvre que les faits sur
    # ARIA elle-meme -- il manquait une regle generale interdisant d'inventer N'IMPORTE
    # QUEL fait precis absent des extraits, quel que soit le sujet.
    for tpl in (_WEB_RECAL_PROMPT_FR, _WEB_RECAL_PROMPT_EN):
        low = tpl.lower()
        assert "incertain" in low
        assert "générale" in low or "general" in low  # "règle générale anti-invention" / "GENERAL ANTI-FABRICATION RULE"
        assert "vagues" in low or "vague" in low


@pytest.mark.asyncio
async def test_web_enhance_declines_when_snippets_too_vague_to_support_specific_facts(monkeypatch):
    # Meme si le LLM (mocke ici) suit desormais la regle generale et refuse d'inventer des
    # faits precis a partir d'extraits vagues, le pipeline ne doit rien transformer en
    # "ACTU verifiee" force -- la reponse honnete (INCERTAIN) doit passer telle quelle.
    from aria_core.knowledge import web_verify as wv

    async def fake_snippets(_q):
        return [
            WebSource(
                text="The latest crypto news includes significant market movements and regulatory updates.",
                url="https://cryptoast.fr/actu",
            ),
            WebSource(
                text="Devenez un expert en crypto. Le guide pour acheter du Bitcoin.",
                url="https://fr.investing.com/news/cryptocurrency-news",
            ),
        ]

    async def fake_chat(*_a, **_k):
        return (
            "FAIT: INCERTAIN\n"
            "REPONSE: Les sources trouvées sont trop génériques (pages d'accueil de sites "
            "d'actualité) pour citer des faits précis vérifiés aujourd'hui.\n"
            "P_VRAI: 0.20\n"
            "P_FAUX: 0.10\n"
            "RAISON: extraits génériques sans détail vérifiable"
        )

    monkeypatch.setattr(wv, "fetch_web_snippets", fake_snippets)
    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)

    reply, meta = await wv.web_enhance_calibrated(
        "quelles sont les dernières news crypto importantes ?",
        None,
        {"p_true": 0.3, "truth": "INCERTAIN"},
        "fr",
        force=True,
    )
    assert reply
    assert "fidji simo" not in reply.lower()
    assert "openai" not in reply.lower()
    assert "mica" not in reply.lower()
    assert meta.get("truth") == "incertain"


@pytest.mark.asyncio
async def test_web_enhance_still_answers_when_source_matches_asked_entity(monkeypatch):
    # Contraste : quand la question porte reellement sur une entite tierce (pas ARIA) et
    # que la source parle bien de cette meme entite, la regle ne doit PAS bloquer une
    # reponse normale -- seul le cas "source parle d'une autre entite que celle
    # interrogee" doit etre refuse, pas toute question sur un tiers.
    from aria_core.knowledge import web_verify as wv

    async def fake_snippets(_q):
        return [
            WebSource(
                text="Brian Armstrong is the CEO of Coinbase.",
                url="https://coinbase.com/about",
            )
        ]

    async def fake_chat(*_a, **_k):
        return (
            "FAIT: VRAI\n"
            "REPONSE: Brian Armstrong est le CEO de Coinbase.\n"
            "P_VRAI: 0.95\n"
            "P_FAUX: 0.02\n"
            "RAISON: source directe Coinbase"
        )

    monkeypatch.setattr(wv, "fetch_web_snippets", fake_snippets)
    monkeypatch.setattr("aria_core.llm.chat_with_context", fake_chat)
    monkeypatch.setattr("aria_core.llm.is_llm_configured", lambda: True)

    reply, meta = await wv.web_enhance_calibrated(
        "qui est le CEO de Coinbase ?",
        None,
        {"p_true": 0.3, "truth": "INCERTAIN"},
        "fr",
        force=True,
    )
    assert reply
    assert "armstrong" in reply.lower()
    assert meta.get("truth") == "vrai"


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