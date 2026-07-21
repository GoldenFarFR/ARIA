import pytest

from aria_core.content.service import _score_faq, _load_faq
from aria_core.grounding import (
    FAQ_DIRECT_SCORE,
    analysis_methodology_reply,
    anti_hallucination_rules,
    aria_brain_status_reply,
    build_verified_facts_block,
    faq_direct_answer,
    format_greeting_reply,
    grounded_for_audience,
    grounded_llm_identity,
    is_analysis_methodology_question,
    is_aria_brain_question,
    is_greeting,
    is_llm_identity_question,
    is_social_chitchat,
    is_pure_casual_smalltalk,
    is_scan_scope_question,
    is_trade_status_question,
    is_why_not_bought_question,
    llm_identity_reply,
    scan_scope_reply,
    should_skip_llm_enhance,
    social_ack_reply,
    unknown_reply,
    why_not_bought_reply,
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


def test_analysis_methodology_question_detects_second_incident_phrasing():
    # Incident réel (18/07) : cette formulation a échappé au regex d'origine et est
    # partie en LLM payant, qui a décrit l'ancien pipeline VC-thesis exclusivement.
    assert is_analysis_methodology_question(
        "quelles sont les conditions alors pour qu'un token t'interesse ?"
    )
    assert is_analysis_methodology_question("what makes a token interesting to you?")
    assert not is_analysis_methodology_question("bonjour")


def test_analysis_methodology_question_tolerates_real_typo():
    # Incident réel (18/07, même soirée) : le premier élargissement exigeait
    # l'orthographe exacte "quelles" -- la vraie question de l'opérateur contenait
    # une faute de frappe ("quuelles") ET un mot supplémentaire ("alors") entre
    # "conditions" et "pour", ratant les DEUX branches du regex d'origine. Le fix
    # ne doit plus dépendre du mot interrogatif exact, seulement de la co-occurrence
    # "condition(s)" + "token/jeton" dans une fenêtre raisonnable.
    assert is_analysis_methodology_question(
        "quuelles sont les conditions alors pour qu'un token tinteresse ?"
    )


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
    # Le repli "?" seul ne suffit toujours pas sans mot-clé de trading -- le garde-fou
    # réel reste le mot-clé, pas la présence d'un point d'interrogation.
    assert not is_trade_status_question("tu as vu le nouveau design du site ?")


def test_trade_status_question_detects_second_real_incident_phrasing():
    """19/07 -- 2e incident réel : "c'est quoi ta these sur lachat de cobot ?" ne
    matchait ni le mot-clé ("achat" != "acheté", "thèse" absent, "AERO" codé en dur
    au lieu d'un vrai mot générique) ni la tournure ("c'est quoi" absente). ARIA a
    répondu "je n'ai pas de thèse" alors que la thèse COBOT était réellement stockée
    en base -- confabulation confirmée par capture opérateur."""
    assert is_trade_status_question("c'est quoi ta these sur lachat de cobot ?")
    assert is_trade_status_question("quelle est ta thèse sur ce trade ?")
    # Le repli "?" seul (sans tournure connue) couvre toute future formulation directe
    # tant qu'un vrai mot-clé de trading est présent.
    assert is_trade_status_question("tu as une thèse là-dessus ?")
    assert is_trade_status_question("t'as acheté combien de tokens ?")


def test_analysis_methodology_reply_cites_real_tools():
    for lang in ("fr", "en"):
        reply = analysis_methodology_reply(lang).lower()
        assert "goplus" in reply
        assert "blockscout" in reply
        assert "rsi" in reply
        assert "geckoterminal" in reply


def test_analysis_methodology_reply_describes_both_pipelines():
    # Incident réel (18/07) : la réponse ne décrivait QUE l'ancien pipeline VC-thesis
    # (safety_screen) alors que le pipeline momentum décide 100% du test live 1M$.
    for lang in ("fr", "en"):
        reply = analysis_methodology_reply(lang).lower()
        assert "safety_screen" in reply
        assert "momentum_entry" in reply


def test_why_not_bought_question_detects_real_incident_phrasing():
    # Incident réel (18/07, chat vision) : "pourquoi tu n'as pas acheté cette
    # divergence sur aeon ?" a reçu une réponse confabulée ("aucun capital réel
    # déployé... pas achat live") alors que le pipeline momentum achète réellement.
    assert is_why_not_bought_question("pourquoi tu n'as pas acheté cette divergence sur aeon ?")
    assert is_why_not_bought_question("pourquoi tu n'achetes pas ce token ?")
    assert is_why_not_bought_question("why didn't you buy that dip?")
    assert is_why_not_bought_question("why haven't you bought this token?")
    assert not is_why_not_bought_question("bonjour")
    assert not is_why_not_bought_question("pourquoi t'as vendu AERO ?")


def test_why_not_bought_reply_never_denies_live_capability():
    for lang in ("fr", "en"):
        reply = why_not_bought_reply(lang).lower()
        assert "momentum_entry" in reply
        assert "goplus" in reply
        assert "aucun capital réel" not in reply
        assert "track-record" not in reply
        assert "no real capital" not in reply


def test_scan_scope_question_detects_real_incident_phrasing():
    # Incident réel (18/07, même soirée que le déploiement du premier correctif) :
    # deux questions distinctes ont de nouveau confabulé, en ré-attribuant tout le scan
    # au pipeline bonding (quasi inactif) au lieu de momentum_entry.py.
    assert is_scan_scope_question(
        "parmis tous se que tu a scanner et refuser lequel avait le meilleur resultat ?"
    )
    assert is_scan_scope_question("je croyais que tu scanner tous les jetons sur base dans dexscreener ?")
    assert is_scan_scope_question("combien de tokens as-tu scanné aujourd'hui ?")
    assert is_scan_scope_question("do you scan all tokens on Base?")
    assert is_scan_scope_question("which token did you reject with the best result?")
    assert not is_scan_scope_question("bonjour")
    assert not is_scan_scope_question("quel est le prix du token")


def test_scan_scope_reply_names_real_mechanism_and_admits_data_gap():
    for lang in ("fr", "en"):
        reply = scan_scope_reply(lang).lower()
        assert "momentum_entry" in reply
        assert "dexscreener" in reply
        assert "bonding_discovery_cycle" in reply
        # Ne doit jamais inventer un "meilleur refus" -- doit admettre l'absence de
        # détail par candidat plutôt que de confabuler une réponse plausible.
        assert "closest" in reply or "proche" in reply


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


async def test_build_verified_facts_block_protects_critical_section_when_oversized(monkeypatch):
    """Incident réel (18/07) : un contenu discrétionnaire plus long (une FAQ élargie
    par un autre correctif le même jour) a fait dépasser 6000 caractères, effaçant
    SILENCIEUSEMENT self-maintenance -- un [:6000] naïf en toute fin coupait toujours
    le DERNIER morceau ajouté, jamais le moins important. Verrouille la priorité :
    même avec un bloc discrétionnaire artificiellement énorme, la section critique
    (admin-only) doit toujours survivre intacte."""
    import aria_core.knowledge.cognitive as cognitive_mod

    class _HugeKnowledgeItem:
        topic = "zhc-test"
        content = "x" * 500

    async def fake_get_approved(limit=16):
        return [_HugeKnowledgeItem() for _ in range(50)]  # largement > 6000 car.

    monkeypatch.setattr(cognitive_mod, "get_approved", fake_get_approved)

    block = await build_verified_facts_block("test query", public=False, lang="fr")

    assert "Operator self-directive" in block
    assert len(block) <= 6000

# ── aria-brain : confabulation sur sa propre mémoire libre (21/07) ─────────────

def test_is_aria_brain_question_detects_real_incident_phrasing():
    assert is_aria_brain_question("tu as écrit dans ton cerveau aujourd'hui ?")
    assert is_aria_brain_question("montre-moi ton aria-brain")
    assert is_aria_brain_question("c'est quoi ta mémoire libre ?")
    assert is_aria_brain_question("have you written in your brain today?")
    assert not is_aria_brain_question("bonjour")
    assert not is_aria_brain_question("quel est le prix du token")


@pytest.fixture
def _isolated_aria_brain_db(tmp_path, monkeypatch):
    from aria_core.skills import aria_brain

    monkeypatch.setattr(aria_brain, "DB_PATH", str(tmp_path / "aria_brain_test.db"))


@pytest.mark.asyncio
async def test_aria_brain_status_reply_disabled(monkeypatch, _isolated_aria_brain_db):
    monkeypatch.delenv("ARIA_BRAIN_ENABLED", raising=False)
    reply = await aria_brain_status_reply("fr")
    assert "désactivée" in reply.lower()
    assert "jamais écrit" in reply.lower()


@pytest.mark.asyncio
async def test_aria_brain_status_reply_enabled_empty_log_no_token_admits_uncertainty(
    monkeypatch, _isolated_aria_brain_db,
):
    """Journal local vide ET aucun token pour vérifier le vrai repo (cas par défaut
    en test -- ``aria_brain_github_token`` vaut "" tant que rien ne l'a configuré) --
    ne doit JAMAIS conclure "rien écrit" sans avoir pu vérifier (incident réel 21/07 :
    un journal local vidé par une migration serveur ne prouve rien sur le vrai repo)."""
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    reply = await aria_brain_status_reply("fr")
    assert "indisponible" in reply.lower()
    assert "rien écrit" not in reply.lower()


@pytest.mark.asyncio
async def test_aria_brain_status_reply_enabled_empty_log_but_repo_has_real_content(
    monkeypatch, _isolated_aria_brain_db,
):
    """Reproduit exactement l'écart réel trouvé le 21/07 : migration VPS le 20/07 a
    vidé le journal local, mais le repo GitHub contenait déjà
    livre/chapitre-01-le-point-zero.md -- le garde ne doit jamais dire "rien écrit"
    dans ce cas, sous peine de confabuler dans le sens inverse."""
    from aria_core.skills import aria_brain

    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")

    async def fake_check():
        return [
            {"path": "livre", "type": "dir"},
            {"path": "livre/chapitre-01-le-point-zero.md", "type": "file"},
        ]

    monkeypatch.setattr(aria_brain, "check_real_repo_content", fake_check)

    reply = await aria_brain_status_reply("fr")
    assert "rien écrit" not in reply.lower()
    assert "chapitre-01-le-point-zero.md" in reply


@pytest.mark.asyncio
async def test_aria_brain_status_reply_enabled_repo_confirmed_empty(monkeypatch, _isolated_aria_brain_db):
    """Le repo est réellement vide (vérifié en direct, pas juste le journal local) --
    seul ce cas doit produire "rien écrit"."""
    from aria_core.skills import aria_brain

    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")

    async def fake_check_empty():
        return []

    monkeypatch.setattr(aria_brain, "check_real_repo_content", fake_check_empty)

    reply = await aria_brain_status_reply("fr")
    assert "rien écrit" in reply.lower()
    assert "vérifié" in reply.lower()


@pytest.mark.asyncio
async def test_aria_brain_status_reply_enabled_with_real_entry(monkeypatch, _isolated_aria_brain_db):
    import aiosqlite

    from aria_core.skills import aria_brain

    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    await aria_brain._ensure_table()
    async with aiosqlite.connect(aria_brain.DB_PATH) as db:
        await db.execute(
            "INSERT INTO aria_brain_log (run_at, path, content_preview, commit_sha, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-07-21T10:00:00+00:00", "livre/chapitre-01.md", "preview", "abc123", "written"),
        )
        await db.commit()

    reply = await aria_brain_status_reply("fr")
    assert "2026-07-21T10:00:00+00:00" in reply
    assert "livre/chapitre-01.md" in reply
