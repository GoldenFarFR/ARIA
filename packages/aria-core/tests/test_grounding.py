from aria_core.content.service import _score_faq, _load_faq
from aria_core.grounding import (
    FAQ_DIRECT_SCORE,
    analysis_methodology_reply,
    anti_hallucination_rules,
    build_verified_facts_block,
    faq_direct_answer,
    format_greeting_reply,
    grounded_for_audience,
    grounded_llm_identity,
    is_analysis_methodology_question,
    is_greeting,
    is_llm_identity_question,
    is_social_chitchat,
    is_pure_casual_smalltalk,
    is_trade_status_question,
    llm_identity_reply,
    should_skip_llm_enhance,
    social_ack_reply,
    unknown_reply,
)


def test_grounded_for_audience_operator_bypass():
    assert grounded_for_audience(public=False) is False
    assert grounded_for_audience(public=True) is True


def test_llm_identity_question_detects_real_incident_phrasing():
    # Incident réel 11/07 : régression exacte du fix du 08/07 sur ce chemin précis
    # (grounded_llm_identity n'est jamais injecté côté opérateur).
    assert is_llm_identity_question("tu fonctionnes avec quel type d'intelligence, un LLM ?")
    assert is_llm_identity_question("es-tu une IA ?")
    assert is_llm_identity_question("are you an LLM?")
    assert is_llm_identity_question("what model are you?")
    assert not is_llm_identity_question("bonjour")
    assert not is_llm_identity_question("quel est le prix du token")


def test_llm_identity_question_does_not_shadow_routing_meta():
    # Ne doit pas capturer les questions de routage TECHNIQUE (provider/API du tour) —
    # llm_routing_meta.is_llm_routing_question reste le mécanisme dédié pour ça.
    assert not is_llm_identity_question("/depth develop quel moteur LLM utilises-tu")
    assert not is_llm_identity_question("route vers virtuals spark")


def test_llm_identity_reply_never_names_a_specific_model():
    for lang in ("fr", "en"):
        reply = llm_identity_reply(lang)
        assert "opus" not in reply.lower()
        assert "grok" not in reply.lower()
        assert "llm" in reply.lower()


def test_llm_identity_reply_matches_grounded_llm_identity_facts():
    # La réponse chat doit rester cohérente avec le bloc système (mêmes faits, ton différent).
    for lang in ("fr", "en"):
        chat_reply = llm_identity_reply(lang).lower()
        system_block = grounded_llm_identity(lang).lower()
        assert ("certitude" in chat_reply or "certainty" in chat_reply)
        assert ("certitude" in system_block or "certainty" in system_block)


def test_analysis_methodology_question_detects_real_incident_phrasing():
    assert is_analysis_methodology_question(
        "comment tu analyses un token, tu utilises de l'IA générative ?"
    )
    assert is_analysis_methodology_question("how do you analyze a token")
    assert is_analysis_methodology_question("quels outils utilises-tu pour analyser")
    assert not is_analysis_methodology_question("bonjour")
    assert not is_analysis_methodology_question("quel est le prix du token")


def test_trade_status_question_detects_real_incident_phrasing():
    # Incident réel 16/07 : question posée juste après une clôture en perte, tombée
    # dans la conversation générale sans accès au registre paper-trading.
    assert is_trade_status_question(
        "tu viens de réaliser un trade perdant qu'est-ce qui c'est passé ?"
    )
    assert is_trade_status_question("pourquoi t'as vendu AERO ?")
    assert is_trade_status_question("comment va le portefeuille ?")
    assert is_trade_status_question("combien il me reste de capital ?")
    assert is_trade_status_question("what happened with this trade?")
    assert is_trade_status_question("why did you sell that position?")


def test_trade_status_question_requires_both_trigger_and_question_form():
    # Un mot-clé de trading seul, sans tournure de question -> pas de faux positif.
    assert not is_trade_status_question("j'ai ouvert une position AERO hier")
    # Une tournure de question générique, sans rapport avec le trading -> pas de
    # faux positif non plus (ne doit jamais injecter le registre hors-propos).
    assert not is_trade_status_question("qu'est-ce qui s'est passé avec le déploiement ?")
    assert not is_trade_status_question("pourquoi le ciel est bleu ?")
    assert not is_trade_status_question("bonjour")


def test_analysis_methodology_reply_cites_real_tools():
    for lang in ("fr", "en"):
        reply = analysis_methodology_reply(lang).lower()
        assert "goplus" in reply
        assert "blockscout" in reply
        assert "rsi" in reply
        assert "geckoterminal" in reply


def test_faq_direct_answer_high_confidence():
    reply, data = faq_direct_answer("What is DEXPulse?")
    assert data["faq_direct"] is True
    assert "DEXPulse" in reply


def test_faq_direct_answer_low_confidence():
    reply, data = faq_direct_answer("xyzzy nonsense question 12345")
    assert data["faq_direct"] is False
    assert reply is None


def test_should_skip_llm_enhance_factual_skills():
    assert should_skip_llm_enhance("faq_content") is True
    assert should_skip_llm_enhance("marketing_comms") is False


def test_anti_hallucination_rules_mention_verified():
    rules = anti_hallucination_rules("en")
    assert "VERIFIED" in rules.upper() or "verified" in rules


def test_unknown_reply_no_invention():
    text = unknown_reply("en")
    assert "verified" in text.lower()


def test_faq_score_threshold():
    items = _load_faq()
    assert items
    score = _score_faq("What is ARIA?", items[0])
    assert score >= 0
    assert FAQ_DIRECT_SCORE >= 4


def test_greeting_detected_gm_hello():
    assert is_greeting("gm") is True
    assert is_greeting("GM!") is True
    assert is_greeting("hello") is True
    assert is_greeting("bonjour") is True
    assert is_greeting("what is dexpulse") is False


def test_operator_greeting_reply_french_bonjour():
    text = format_greeting_reply("hello", "en", public=False)
    assert "Bonjour" in text
    assert "Vanguard" in text
    assert "DEXPulse" not in text


def test_gm_greeting_prefix():
    text = format_greeting_reply("gm", "fr", public=False)
    assert text.startswith("GM !")
    assert "Bonjour" in text


def test_social_chitchat_detected():
    assert is_social_chitchat("Merci pour les félicitations !")
    assert is_social_chitchat("Bravo pour le succès, continue comme ça")
    assert not is_social_chitchat("Qu'est-ce que DEXPulse ?")


def test_social_ack_no_revenue_claims():
    text = social_ack_reply("fr")
    # Balanced repartie style: light, can gently open to product/build without hard revenue claims
    assert "merci" in text.lower() or "vanguard" in text.lower() or "construire" in text.lower() or "dis-moi" in text.lower()
    assert "revenue" not in text.lower() and "revenu" not in text.lower()


def test_pure_casual_smalltalk_detects_daily_life():
    assert is_pure_casual_smalltalk("Il fait beau chez toi ?")
    assert is_pure_casual_smalltalk("T'as mangé quoi aujourd'hui ?")
    assert is_pure_casual_smalltalk("Raconte une blague nulle")
    assert is_pure_casual_smalltalk("Ça va ? Ta journée ?")
    assert not is_pure_casual_smalltalk("Qu'est-ce que le site Vanguard ?")
    assert not is_pure_casual_smalltalk("On en est où sur le revenue goal ?")


async def test_build_verified_facts_block_includes_self_maintenance_when_not_public():
    """Balayage code mort du 15/07 : self_maintenance_context_for_brain() (self_maintenance.py)
    était écrite mais jamais injectée dans le contexte LLM -- filet pour les ordres opérateur
    (profil X/bannière/avatar) qui échappent au classifieur regex strict de handle_operator_self_message.
    Même point d'insertion que les directives opérateur, admin-only comme elles."""
    block = await build_verified_facts_block("test query", public=False, lang="fr")
    assert "Operator self-directive" in block


async def test_build_verified_facts_block_omits_self_maintenance_when_public():
    block = await build_verified_facts_block("test query", public=True, lang="fr")
    assert "Operator self-directive" not in block