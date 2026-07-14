"""Rendu du rapport VC HTML — design B4 (hero nocturne or/émeraude + corps ivoire).

Vérifie la présence des sections, l'échappement HTML de tout contenu dynamique
(défense injection = extension du dôme), et le mini-markdown.
"""
from __future__ import annotations

from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_report import (
    _format_serial,
    _render_markdown_body,
    email_subject,
    render_html_report,
    report_integrity,
)

ADDR = "0x" + "a" * 40
_GEN = "2026-07-06 18:00 UTC"


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR,
        potentiel=7,
        risque="MODÉRÉ",
        these="Traction on-chain réelle et tokenomics saine.",
        recommandation="BUY",
        taille_pct=5.0,
        entree="marché",
        invalidation="perte support $5k",
        cible="x2 6 mois",
        donnees_insuffisantes=["équipe", "levées de fonds"],
        rapport_detaille="## Potentiel\n- Techno différenciante\n**Moat** solide.\n\n## Risque\nLiquidité faible.",
        security_score=60,
        lite_verdict="CAUTION",
        llm_used=True,
        resume_executif="Infrastructure Base à moat réel, entrée mesurée recommandée.",
        confiance_globale="moyenne",
        scenarios=[
            {"nom": "bull", "cible": "x3", "probabilite": 30, "confiance": "moyenne"},
            {"nom": "base", "cible": "x1.5", "probabilite": 50, "confiance": "haute"},
            {"nom": "bear", "cible": "-40%", "probabilite": 20, "confiance": "moyenne"},
        ],
        upside_pct=180.0,
        downside_pct=45.0,
        symbol="ATLAS",
    )
    base.update(kw)
    return VCResult(**base)


def test_report_contains_core_sections():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "ARIA Vanguard ZHC" in out
    assert "ATLAS" in out  # symbole utilisé comme titre
    assert "BUY" in out
    assert "Ordre proposé" in out
    assert "Thèse d&#x27;investissement" in out or "Thèse d'investissement" in out
    assert "Données insuffisantes" in out
    assert "proposition soumise à validation humaine" in out
    assert _GEN in out


def test_report_title_falls_back_to_contract_when_symbol_missing():
    out = render_html_report(_result(symbol=""), generated_at=_GEN)
    assert ADDR[:10] in out


def test_report_renders_markdown_body():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "<h3" in out  # ## Potentiel
    assert "<li" in out  # - Techno
    assert "<strong>Moat</strong>" in out  # **Moat**


def test_report_escapes_hostile_content():
    """Un contenu LLM hostile (issu de données non fiables) doit être neutralisé."""
    hostile = _result(
        these="<script>alert('xss')</script>",
        rapport_detaille="## <img src=x onerror=alert(1)>\nTexte normal.",
        entree="<b>marché</b>",
    )
    out = render_html_report(hostile, generated_at=_GEN)
    assert "<script>" not in out
    assert "alert('xss')" not in out or "&lt;script&gt;" in out
    assert "&lt;script&gt;" in out
    assert "&lt;img" in out
    assert "<b>marché</b>" not in out


def test_report_escapes_hostile_scenario_and_gap_content():
    hostile = _result(
        scenarios=[{"nom": "bull", "cible": "<script>x</script>", "probabilite": 50, "confiance": "haute"}],
        donnees_insuffisantes=["<iframe src=evil></iframe>"],
        liens_projet=[{"label": "<script>y</script>", "url": "https://atlas.example/<b>"}],
    )
    out = render_html_report(hostile, generated_at=_GEN)
    assert "<script>" not in out
    assert "<iframe" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;iframe" in out


def test_report_no_order_block_when_not_actionable():
    out = render_html_report(_result(recommandation="WATCH", taille_pct=0.0), generated_at=_GEN)
    assert "Ordre proposé" not in out


def test_report_no_rr_block_when_rr_is_none():
    """Sans upside/downside estimables, aucun ratio R/R n'est fabriqué."""
    out = render_html_report(_result(upside_pct=None, downside_pct=None), generated_at=_GEN)
    assert "Ratio récompense-risque" not in out
    assert "Potentiel haussier" not in out


def test_report_shows_rr_block_when_available():
    out = render_html_report(_result(upside_pct=180.0, downside_pct=45.0), generated_at=_GEN)
    assert "Ratio récompense-risque" in out
    assert "4.0" in out  # 180/45
    assert "+180" in out
    assert "45" in out


def test_report_fallback_note_when_llm_disabled():
    out = render_html_report(
        _result(llm_used=False, potentiel=None, recommandation="WATCH", taille_pct=0.0),
        generated_at=_GEN,
    )
    assert "Analyse qualitative LLM indisponible" in out
    assert "Potentiel n/a" in out  # pastille : la donnée absente n'est jamais estimée


def test_email_subject_format():
    subj = email_subject(_result())
    assert "ARIA Vanguard ZHC" in subj
    assert "BUY" in subj
    assert "7/10" in subj


def test_email_subject_includes_date_when_provided():
    subj = email_subject(_result(), generated_at=_GEN)
    assert "2026-07-06" in subj  # tri facile en boîte mail une fois abonné à plusieurs rapports


def test_email_subject_without_date_has_no_date_prefix():
    subj = email_subject(_result())
    assert subj == "[ARIA Vanguard ZHC] Analyse VC · BUY · Potentiel 7/10 · 0xaaaaaaaa…"


def test_email_subject_includes_report_number_when_provided():
    subj = email_subject(_result(), generated_at=_GEN, report_number=2)
    assert "n°2" in subj
    assert subj.index("n°2") < subj.index("2026-07-06")  # numéro avant la date


def test_report_shows_report_number_when_provided():
    out = render_html_report(_result(), generated_at=_GEN, report_number=3)
    assert "Rapport n°3" in out


def test_report_no_report_number_line_without_it():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Rapport n°" not in out


def test_format_serial_pads_and_splits():
    assert _format_serial(47) == "00.047"
    assert _format_serial(2) == "00.002"
    assert _format_serial(12345) == "12.345"
    assert _format_serial(0) == "00.000"


def test_report_shows_series_number_when_provided():
    out = render_html_report(_result(), generated_at=_GEN, series_number=47)
    assert "Série 00.047" in out


def test_report_no_series_line_without_it():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Série" not in out


def test_report_meta_line_order_series_then_report_then_date():
    out = render_html_report(_result(), generated_at=_GEN, series_number=47, report_number=2)
    assert out.index("Série 00.047") < out.index("Rapport n°2") < out.index(_GEN)


def test_report_has_site_link_top_left():
    out = render_html_report(_result(), generated_at=_GEN)
    assert 'href="https://ariavanguardzhc.com"' in out
    assert "ariavanguardzhc.com" in out


# ----------------------- capital_usd → montants en $ -----------------------


def test_report_capital_usd_shows_dollar_amounts_in_order_block():
    out = render_html_report(_result(taille_pct=5.0), generated_at=_GEN, capital_usd=1500)
    assert "Capital client" in out
    assert "$1,500" in out
    assert "$75" in out  # 5% de 1500


def test_report_no_capital_line_without_capital_usd():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Capital client" not in out


def test_report_dollar_potential_section_with_capital():
    out = render_html_report(
        _result(taille_pct=5.0, upside_pct=180.0, downside_pct=45.0),
        generated_at=_GEN,
        capital_usd=1500,
    )
    assert "Potentiel en $" in out
    assert "+$135" in out  # gain = 75 * 180% = 135


def test_report_dollar_potential_amounts_are_consistent_with_capital():
    out = render_html_report(
        _result(taille_pct=5.0, upside_pct=180.0, downside_pct=45.0),
        generated_at=_GEN,
        capital_usd=1500,
    )
    position = 1500 * 5.0 / 100  # 75
    gain = position * 180.0 / 100  # 135
    loss = position * 45.0 / 100  # 33.75 -> "$34" arrondi
    assert f"${position:,.0f}" in out
    assert f"+${gain:,.0f}" in out
    assert f"${loss:,.0f}" in out


def test_report_no_dollar_potential_section_without_capital():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Potentiel en $" not in out


def test_report_no_dollar_potential_section_without_upside_downside():
    out = render_html_report(
        _result(upside_pct=None, downside_pct=None), generated_at=_GEN, capital_usd=1500
    )
    assert "Potentiel en $" not in out


def test_report_no_dollar_potential_section_when_not_buy():
    out = render_html_report(
        _result(recommandation="WATCH", taille_pct=0.0), generated_at=_GEN, capital_usd=1500
    )
    assert "Potentiel en $" not in out


# ----------------------- références projet (site, X, Telegram…) -----------------------


def test_report_shows_project_links_when_present():
    out = render_html_report(
        _result(liens_projet=[{"label": "Website", "url": "https://atlas.example"}]),
        generated_at=_GEN,
    )
    assert "Références" in out
    assert "href='https://atlas.example'" in out
    assert "Website" in out


def test_report_no_project_links_shows_fallback_text():
    out = render_html_report(_result(liens_projet=[]), generated_at=_GEN)
    assert "Aucun lien officiel disponible" in out


def test_report_rejects_non_http_link_even_if_smuggled_in():
    """Défense en profondeur ultime : même une URL hostile déjà présente dans
    VCResult (upstream compromis) ne doit jamais devenir un <a href> cliquable."""
    hostile = _result(liens_projet=[{"label": "Faux site", "url": "javascript:alert(1)"}])
    out = render_html_report(hostile, generated_at=_GEN)
    assert "javascript:" not in out
    assert "Aucun lien officiel disponible" in out


def test_report_escapes_project_link_label_and_url():
    hostile = _result(
        liens_projet=[{"label": "<script>x</script>", "url": "https://atlas.example/<b>"}]
    )
    out = render_html_report(hostile, generated_at=_GEN)
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_markdown_body_empty_is_safe():
    assert "Aucun contenu" in _render_markdown_body("")


def test_markdown_body_escapes_plain_injection():
    out = _render_markdown_body("Texte avec <iframe> injecté")
    assert "<iframe>" not in out
    assert "&lt;iframe&gt;" in out


# ----------------------- copyright / filigrane / empreinte -----------------------


def test_report_has_copyright_and_confidentiality():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Tous droits réservés" in out
    assert "confidentiel" in out.lower()
    assert "revente interdites" in out


def test_report_watermark_present_with_recipient():
    out = render_html_report(_result(), generated_at=_GEN, recipient="agentaria.zhc@gmail.com")
    assert "Édition personnelle" in out
    assert "agentaria.zhc@gmail.com" in out


def test_report_no_watermark_without_recipient():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Édition personnelle" not in out


def test_report_integrity_hash_present_and_deterministic():
    r = _result()
    out = render_html_report(r, generated_at=_GEN)
    ref_id, full_hash = report_integrity(r, generated_at=_GEN)
    assert len(full_hash) == 64  # SHA-256 hex
    assert ref_id in out
    assert full_hash in out
    # Déterministe : même entrée → même empreinte.
    ref_id2, full_hash2 = report_integrity(r, generated_at=_GEN)
    assert (ref_id, full_hash) == (ref_id2, full_hash2)


def test_report_integrity_changes_with_content():
    base = report_integrity(_result(), generated_at=_GEN)
    altered = report_integrity(_result(rapport_detaille="## Autre contenu\nModifié."), generated_at=_GEN)
    assert base != altered  # toute altération change l'empreinte


def test_report_integrity_changes_with_recipient():
    a = report_integrity(_result(), generated_at=_GEN, recipient="a@x.com")
    b = report_integrity(_result(), generated_at=_GEN, recipient="b@x.com")
    assert a != b  # empreinte par destinataire → fuite traçable


def test_report_recipient_watermark_is_escaped():
    out = render_html_report(_result(), generated_at=_GEN, recipient="<script>@x.com")
    assert "<script>@x.com" not in out
    assert "&lt;script&gt;" in out


# ----------------------- éléments visuels -----------------------


def test_report_embeds_emblem():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "data:image/png;base64," in out  # emblème embarqué, pas d'asset externe


def test_report_has_badges():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Confiance moyenne" in out
    assert "Potentiel 7/10" in out
    assert "Risque MODÉRÉ" in out


def test_report_has_tldr():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "En bref" in out
    assert "moat réel" in out


def test_report_no_tldr_when_empty():
    out = render_html_report(_result(resume_executif=""), generated_at=_GEN)
    assert "En bref" not in out


def test_report_has_scenarios():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Haussier" in out
    assert "Central · Référence" in out
    assert "Baissier" in out
    assert "Probabilité&nbsp;50%" in out


def test_report_no_scenarios_section_when_empty():
    out = render_html_report(_result(scenarios=[]), generated_at=_GEN)
    assert "Scénarios" not in out


def test_report_no_value_scale_bar_without_cible_multiple():
    # Audit #11 : sans cible_multiple (comme les fixtures existantes), aucune barre
    # "échelle commune" ni sa note -- dégradation douce, rien de nouveau visible.
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Échelle commune" not in out


def test_report_shows_value_scale_bar_when_at_least_two_multiples_known():
    out = render_html_report(
        _result(scenarios=[
            {"nom": "bull", "cible": "x5", "cible_multiple": 5.0, "probabilite": 20, "confiance": "moyenne"},
            {"nom": "base", "cible": "x1.5", "cible_multiple": 1.5, "probabilite": 50, "confiance": "haute"},
            {"nom": "bear", "cible": "-40%", "probabilite": 30, "confiance": "moyenne"},  # non chiffré
        ]),
        generated_at=_GEN,
    )
    assert "Échelle commune" in out
    # bull (5.0/5.0=100%) et base (1.5/5.0=30%) doivent avoir des largeurs distinctes.
    assert 'width="100%"' in out
    assert 'width="30%"' in out


def test_report_omits_value_scale_bar_with_fewer_than_two_multiples():
    out = render_html_report(
        _result(scenarios=[
            {"nom": "bull", "cible": "x5", "cible_multiple": 5.0, "probabilite": 20, "confiance": "moyenne"},
            {"nom": "base", "cible": "x1.5", "probabilite": 50, "confiance": "haute"},
            {"nom": "bear", "cible": "-40%", "probabilite": 30, "confiance": "moyenne"},
        ]),
        generated_at=_GEN,
    )
    assert "Échelle commune" not in out


def test_report_has_methodology_and_sources():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Méthodologie &amp; sources" in out  # titre statique, HTML-échappé comme tout le reste
    assert "DexScreener" in out
    assert "Blockscout" in out


def test_report_no_projection_section():
    """Le moteur de projection temporelle (comparables historiques) n'existe pas
    encore (tâche #5) — aucun montant projeté ne doit être inventé."""
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Projection temporelle" not in out


def test_report_is_mobile_responsive():
    out = render_html_report(_result(), generated_at=_GEN)
    assert 'name="viewport"' in out
    assert "@media (max-width:480px)" in out
    assert 'class="kpi"' in out
    assert 'class="stack sc-card"' in out
    assert "display:block !important" in out


def test_report_has_no_radial_gradient():
    """Perf mobile (Gmail WebView) : un radial-gradient sur une grande surface
    force une re-rastérisation à chaque frame de scroll (rendu saccadé). Le
    hero et le pied « certificat » doivent rester en linear-gradient/solide."""
    out = render_html_report(_result(), generated_at=_GEN)
    assert "radial-gradient" not in out


# ----------------------- tiers (premium / standard) -----------------------


def test_report_premium_is_the_default_tier():
    default_out = render_html_report(_result(), generated_at=_GEN)
    explicit_out = render_html_report(_result(), generated_at=_GEN, tier="premium")
    assert default_out == explicit_out
    assert "RAPPORT PREMIUM" in default_out
    assert "Analyse détaillée" in default_out
    assert "Méthodologie &amp; sources" in default_out
    assert "Références" in default_out


def test_report_premium_tier_shows_full_content():
    out = render_html_report(_result(), generated_at=_GEN, tier="premium")
    assert "RAPPORT PREMIUM" in out
    assert "#0a0e1a" in out or "#101a2e" in out
    assert "<h3" in out  # ## Potentiel du rapport_detaille
    assert "Méthodologie &amp; sources" in out
    assert "Références" in out


def test_report_standard_tier_uses_rose_theme_and_hides_premium_content():
    out = render_html_report(_result(), generated_at=_GEN, tier="standard")
    assert "RAPPORT STANDARD" in out
    assert "#1c0e18" in out or "#2a1622" in out
    assert "<h3" not in out  # analyse détaillée (## Potentiel) masquée
    assert "Méthodologie" not in out
    assert "Références" not in out
    assert "réservées à l&#x27;édition Premium" in out or "réservées à l'édition Premium" in out
    # Le reste du rapport reste présent en standard.
    assert "En bref" in out
    assert "Ordre proposé" in out
    assert "Thèse d&#x27;investissement" in out or "Thèse d'investissement" in out
    assert "Scénarios" in out
    assert "Données insuffisantes" in out
    assert "Tous droits réservés" in out


def test_report_invalid_tier_falls_back_to_premium():
    out = render_html_report(_result(), generated_at=_GEN, tier="gratuit")
    assert "RAPPORT PREMIUM" in out
    assert "RAPPORT STANDARD" not in out
    assert "Analyse détaillée" in out
    assert "Méthodologie &amp; sources" in out


def test_report_standard_tier_still_escapes_hostile_content():
    hostile = _result(
        these="<script>alert('xss')</script>",
        rapport_detaille="## <img src=x onerror=alert(1)>\nTexte normal.",
        entree="<b>marché</b>",
    )
    out = render_html_report(hostile, generated_at=_GEN, tier="standard")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>marché</b>" not in out
    # Contenu hostile du rapport détaillé : de toute façon masqué en standard.
    assert "<img src=x" not in out


# ── Contexte marché macro (tâche #14) — data-gated, premium uniquement ──────

_MARKET_CTX = {
    "label": "hausse (markup)", "since": "2024-04-20",
    "change_pct": 42.0, "cycle_name": "cycle halving 2024->en cours",
}


def test_report_market_context_section_present_when_available():
    out = render_html_report(_result(market_context=_MARKET_CTX), generated_at=_GEN)
    assert "Contexte marché" in out
    assert "Bitcoin" in out
    assert "hausse (markup)" in out
    assert "20/04/2024" in out or "2024-04-20" in out
    assert "loi de marché" in out.lower()


def test_report_no_market_context_section_without_data():
    out = render_html_report(_result(market_context=None), generated_at=_GEN)
    assert "Contexte marché" not in out


def test_report_market_context_hidden_in_standard_tier():
    out = render_html_report(_result(market_context=_MARKET_CTX), generated_at=_GEN, tier="standard")
    assert "Contexte marché" not in out


def test_report_market_context_escapes_hostile_content():
    hostile = _result(market_context={
        "label": "<script>alert(1)</script>", "since": "2024-04-20",
        "change_pct": 1.0, "cycle_name": "x",
    })
    out = render_html_report(hostile, generated_at=_GEN)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ── Contexte actions/ETF/matières premières (tâche #14 suite, 13/07) — data-gated,
# premium uniquement, indépendant du contexte BTC ci-dessus ────────────────

_EQUITIES_CTX = {
    "spy": {"price": 512.34, "change_pct": 1.23, "date": "2026-07-13", "stale": False},
    "qqq": {"price": 481.0, "change_pct": -0.5, "date": "2026-07-13", "stale": False},
    "commodities": {"value": 104.2, "unit": "index points", "date": "2026-07-13", "stale": False},
}


def test_report_equities_context_section_present_when_available():
    out = render_html_report(_result(market_context_equities=_EQUITIES_CTX), generated_at=_GEN)
    assert "Actions et matières premières" in out
    assert "SPY" in out
    assert "QQQ" in out
    assert "512.34" in out
    assert "104.2" in out
    assert "indice natif" in out.lower()


def test_report_no_equities_context_section_without_data():
    out = render_html_report(_result(market_context_equities=None), generated_at=_GEN)
    assert "Actions et matières premières" not in out


def test_report_equities_context_partial_data_only_renders_available_lines():
    """SPY seul disponible (QQQ/commodities absents) -- rendu partiel, jamais de
    ligne vide ni de valeur inventée pour combler."""
    partial = {"spy": {"price": 500.0, "change_pct": 0.1, "date": "2026-07-13", "stale": False}}
    out = render_html_report(_result(market_context_equities=partial), generated_at=_GEN)
    assert "Actions et matières premières" in out
    assert "SPY" in out
    assert "QQQ" not in out


def test_report_equities_context_hidden_in_standard_tier():
    out = render_html_report(_result(market_context_equities=_EQUITIES_CTX), generated_at=_GEN, tier="standard")
    assert "Actions et matières premières" not in out


def test_report_equities_context_escapes_hostile_content():
    hostile = _result(market_context_equities={
        "commodities": {"value": "<script>alert(1)</script>", "unit": "x", "date": "x", "stale": False},
    })
    out = render_html_report(hostile, generated_at=_GEN)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_report_equities_context_independent_from_btc_context():
    out = render_html_report(
        _result(market_context=_MARKET_CTX, market_context_equities=None), generated_at=_GEN,
    )
    assert "Contexte marché" in out
    assert "Actions et matières premières" not in out


# ── Langue (FR par défaut, EN additif — libellés fixes uniquement) ──────────

def test_report_default_lang_is_fr_byte_identical_labels():
    out = render_html_report(_result(), generated_at=_GEN)
    assert 'html lang="fr"' in out
    assert "Ordre proposé" in out
    assert "Analyse détaillée" in out
    assert "Méthodologie &amp; sources" in out
    assert "Confidentiel" in out


def test_report_lang_en_translates_fixed_labels():
    out = render_html_report(_result(), generated_at=_GEN, lang="en")
    assert 'html lang="en"' in out
    assert "Proposed order" in out
    assert "Detailed analysis" in out
    assert "Methodology &amp; sources" in out
    assert "Confidential" in out
    # Jamais un mélange : les libellés fixes FR ne doivent plus apparaître en EN.
    assert "Ordre proposé" not in out
    assert "Analyse détaillée" not in out


def test_report_lang_en_translates_risk_and_confidence_codes():
    out = render_html_report(_result(risque="ÉLEVÉ", confiance_globale="haute"), generated_at=_GEN, lang="en")
    assert "Risk HIGH" in out
    assert "Confidence high" in out


def test_report_lang_en_translates_scenario_titles():
    out = render_html_report(_result(), generated_at=_GEN, lang="en")
    assert "Bullish" in out
    assert "Bearish" in out
    assert "Central" in out and "Reference" in out


def test_report_lang_invalid_falls_back_to_fr():
    out = render_html_report(_result(), generated_at=_GEN, lang="zz")
    assert 'html lang="fr"' in out
    assert "Ordre proposé" in out


def test_email_subject_lang_en():
    subj = email_subject(_result(), generated_at=_GEN, lang="en")
    assert "VC Analysis" in subj
    assert "Analyse VC" not in subj


def test_email_teaser_html_never_leaks_thesis_or_detailed_report():
    from aria_core.skills.vc_report import render_email_teaser_html

    secret_thesis = "Thèse ultra-confidentielle jamais montrée ZQXJ."
    result = _result(these=secret_thesis, rapport_detaille="## Détail\nContenu très confidentiel WVKQ.")
    out = render_email_teaser_html(result, lang="fr")
    assert secret_thesis not in out
    assert "WVKQ" not in out
    assert result.recommandation in out


def test_email_teaser_text_lang_en():
    from aria_core.skills.vc_report import email_teaser_text

    out = email_teaser_text(_result(), lang="en")
    assert "attached" in out.lower()
    assert "Potential" in out
