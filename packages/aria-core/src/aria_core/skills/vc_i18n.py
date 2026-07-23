"""Internationalization of the VC surface — FR by default, EN additive.

Safety principle: **French remains the validated historical behavior**
(byte-identical output to before). English is **purely additive** — only
(a) the display of fixed labels and (b) a language directive added to the
LLM prompt change. **Numbers, scores, addresses, and enum codes**
(`risque`, `confiance`, `recommandation`) stay identical: never a
re-interpretation, only a translation of the prose.
"""
from __future__ import annotations

from aria_core.locale import LANG_EN, LANG_FR

SUPPORTED_VC_LANGS = (LANG_FR, LANG_EN)


def norm_lang(lang: str | None) -> str:
    """Normalizes to a supported language; defaults to FR (never an error)."""
    value = (lang or "").strip().lower()
    return value if value in SUPPORTED_VC_LANGS else LANG_FR


# --- Language directive for the LLM (analysis + judge) -------------------------
# Added ONLY in English. In FR the prompt stays unchanged (empty string).
_LLM_DIRECTIVE_EN = (
    "\n\nOUTPUT LANGUAGE — IMPORTANT: write ALL free-text / prose values of the "
    "JSON in ENGLISH (summaries, theses, entry/invalidation/target descriptions, "
    "detailed report, scenario targets, lists of missing data, judge notes, "
    "strengths, weaknesses, unsupported claims, etc.). "
    "But do NOT translate any ENUM / CODE token — keep these EXACTLY as the schema "
    "specifies, in the original letters: risque (FAIBLE|MODÉRÉ|ÉLEVÉ|EXTRÊME), "
    "confiance (haute|moyenne|faible), verdict (solide|fragile|rejeté), "
    "recommandation_juge (garder|ajuster|rejeter), recommandation "
    "(BUY|WATCH|SELL|AVOID). Keep every number identical. "
    "For a missing field write 'insufficient data' (not 'donnée insuffisante')."
)


def llm_language_directive(lang: str) -> str:
    """Suffix to concatenate to the system prompt. Empty in FR (no regression)."""
    return _LLM_DIRECTIVE_EN if norm_lang(lang) == LANG_EN else ""


# --- Translation of risk codes for display --------------------------------------
_RISK_EN = {
    "FAIBLE": "LOW",
    "MODÉRÉ": "MODERATE",
    "MODERE": "MODERATE",
    "ÉLEVÉ": "HIGH",
    "ELEVE": "HIGH",
    "EXTRÊME": "EXTREME",
    "EXTREME": "EXTREME",
}


def risk_label(risque: str, lang: str) -> str:
    """Translates the risk code for display (identity in FR)."""
    if norm_lang(lang) == LANG_EN:
        return _RISK_EN.get((risque or "").strip().upper(), risque)
    return risque


# --- Telegram order labels (format_telegram_order) ------------------------------
def order_strings(lang: str) -> dict:
    """Fixed Telegram order labels. FR = exactly the existing behavior."""
    if norm_lang(lang) == LANG_EN:
        return {
            "order_header": "📊 ARIA — Proposed order",
            "analysis_header": "📊 ARIA — Analysis (no order)",
            "token": "Token",
            "reco": "Reco",
            "potential": "Potential",
            "risk": "Risk",
            "rr": "Risk/reward: aim +{up:.0f}% for {down:.0f}% risked (ratio {rr}, not a gain multiple)",
            "rr_tight_stop": "Note: tight stop ({down:.0f}%) inflates this ratio, easy to hit.",
            "size": "Suggested size : {pct:.1f}% of capital",
            "size_usd": " (≈ ${pos:,.0f} of ${cap:,.0f})",
            "entry": "Entry",
            "invalidation": "Invalidation",
            "target": "Target",
            "thesis": "Thesis",
            "no_llm": "⚠️ Qualitative analysis unavailable (LLM off) — quantitative signals only.",
            "disclaimer": "⚠️ Proposal — manual validation and execution on your Tangem. No automatic execution.",
            "na": "n/a",
        }
    return {
        "order_header": "📊 ARIA — Ordre proposé",
        "analysis_header": "📊 ARIA — Analyse (pas d'ordre)",
        "token": "Token",
        "reco": "Reco",
        "potential": "Potentiel",
        "risk": "Risque",
        "rr": "Risque/récompense : viser +{up:.0f}% pour {down:.0f}% risqué (ratio {rr}, pas un multiple de gain)",
        "rr_tight_stop": "Note : stop serré ({down:.0f}%), ce ratio est flatté et facile à toucher.",
        "size": "Taille suggérée : {pct:.1f}% du capital",
        "size_usd": " (≈ ${pos:,.0f} sur ${cap:,.0f})",
        "entry": "Entrée",
        "invalidation": "Invalidation",
        "target": "Cible",
        "thesis": "Thèse",
        "no_llm": "⚠️ Analyse qualitative indisponible (LLM désactivé) — signaux quantitatifs seuls.",
        "disclaimer": "⚠️ Proposition — validation et exécution manuelle sur ta Tangem. Aucune exécution automatique.",
        "na": "n/a",
    }


# --- Scaffold messages for the /vc flow (Telegram) ------------------------------
def scaffold_strings(lang: str) -> dict:
    """Fixed messages for the /vc handler (status, test mode, usage)."""
    if norm_lang(lang) == LANG_EN:
        return {
            "analyzing": "⏳ VC analysis running (Spark deep + on-chain data)...",
            "usage": (
                "Usage: /vc <contract_address>\n"
                "Full VC analysis (Potential, Risk, Thesis, proposed order).\n"
                "Invalid address — expected: 0x followed by 40 hex characters."
            ),
            "busy": "⏳ An analysis is already running — yours starts right after.",
            "overloaded": "⚠️ Too many VC analyses queued (max load). Try again in a minute.",
            "test_reasoning": "🧪 TEST MODE — Full reasoning:\n\n",
            "test_truncated": "\n\n… (reasoning truncated for Telegram)",
            "test_footer": (
                "🧪 TEST MODE — not sent, not recorded.\n"
                "No email sent, no prediction added to the track record, counters unchanged."
            ),
            "no_reasoning": "(no detailed reasoning available)",
            "lang_set": "🌐 Analysis language set to English.",
            "lang_current": "🌐 Current analysis language: {lang}. Change it with /langue fr | /langue en.",
            "lang_usage": "Usage: /langue fr | /langue en",
        }
    return {
        "analyzing": "⏳ Analyse VC en cours (Spark deep + données on-chain)...",
        "usage": (
            "Usage : /vc <adresse_contrat>\n"
            "Analyse VC complète (Potentiel, Risque, Thèse, ordre proposé).\n"
            "Adresse invalide — attendu : 0x suivi de 40 caractères hexadécimaux."
        ),
        "busy": "⏳ Une analyse est déjà en cours — la tienne s'enchaîne juste après.",
        "overloaded": "⚠️ Trop d'analyses VC en attente (charge maximale). Réessaie dans une minute.",
        "test_reasoning": "🧪 MODE TEST — Raisonnement complet :\n\n",
        "test_truncated": "\n\n… (raisonnement tronqué pour Telegram)",
        "test_footer": (
            "🧪 MODE TEST — non envoyé, non enregistré.\n"
            "Aucun email émis, aucune prédiction ajoutée au track-record, compteurs inchangés."
        ),
        "no_reasoning": "(aucun raisonnement détaillé disponible)",
        "lang_set": "🌐 Langue des analyses réglée sur le français.",
        "lang_current": "🌐 Langue actuelle des analyses : {lang}. Change avec /langue fr | /langue en.",
        "lang_usage": "Usage : /langue fr | /langue en",
    }


# --- Confidence labels for display (same principle as risk_label) --------------
_CONFIDENCE_EN = {"haute": "high", "moyenne": "moderate", "faible": "low"}


def confidence_label(confiance: str, lang: str) -> str:
    """Translates the confidence code for display (identity in FR)."""
    if norm_lang(lang) == LANG_EN:
        return _CONFIDENCE_EN.get((confiance or "").strip().lower(), confiance)
    return confiance


# --- Fixed labels for the detailed VC report (HTML + PDF) -----------------------
# A single dictionary, reused by vc_report.py (HTML email) AND vc_report_pdf.py
# (secured PDF attachment) — same principle FR=identical to existing / EN additive.
def report_strings(lang: str) -> dict:
    """Fixed labels for the detailed VC report — never the LLM's free prose
    (thesis, summary, reasoning), which is already generated in the target language by
    the LLM itself via ``llm_language_directive``. Here: only structural
    labels (section titles, table headers, fixed disclaimers)."""
    if norm_lang(lang) == LANG_EN:
        return {
            "tier_premium_label": "PREMIUM REPORT",
            "tier_standard_label": "STANDARD REPORT",
            "html_title": "ARIA Vanguard ZHC · Research note · {title}",
            "confidential": "Confidential",
            "research_note_kicker": "Research note · Investment analysis",
            "network_label": "BASE NETWORK",
            "meta_series": "Series {n}",
            "meta_report_num": "Report #{n}",
            "meta_generated": "Generated on {date}",
            "meta_issued_by": "Issued by ARIA Vanguard ZHC",
            "preheader_suffix": "ARIA Vanguard ZHC research note",
            "refs_none": "No official link available for this token.",
            "refs_title": "References: verify for yourself",
            "refs_disclaimer": "Links declared by the project (source: DexScreener). Not verified by ARIA.",
            "potential_label": "Potential {n}/10",
            "potential_na": "Potential n/a",
            "confidence_prefix": "Confidence {v}",
            "risk_prefix": "Risk {v}",
            "rr_qualifier_strong": "Very favorable asymmetry",
            "rr_qualifier_good": "Favorable asymmetry",
            "rr_qualifier_balanced": "Balanced asymmetry",
            "rr_qualifier_weak": "Unfavorable asymmetry",
            "rr_caption": (
                "{qualifier}: aim for +{upside:.0f}% against {downside:.0f}% risked. "
                "Reward-to-risk distance ratio, not a gain multiple."
            ),
            "rr_tight_stop": " Tight stop ({downside:.0f}%): ratio is flattered, easy to hit.",
            "rr_upside_label": "Upside potential",
            "rr_ratio_label": "Reward-risk ratio",
            "rr_downside_label": "Downside risk",
            "order_section": "Proposed order",
            "order_reco": "Recommendation",
            "order_size": "Suggested size",
            "order_size_value": "{pct:.1f}% of capital",
            "order_capital_to_position": "Client capital → position",
            "order_entry": "Entry",
            "order_invalidation": "Invalidation",
            "order_target": "Target",
            "order_disclaimer": "Proposal subject to human validation before any execution.",
            "dollar_section": "Potential in $",
            "dollar_position_line": "Position {position} → {target} at target",
            "dollar_gain_label": "Gain",
            "dollar_risk_line": "Risk incurred on invalidation ({inval})",
            "dollar_scale_note": "Common scale: bars proportional to $ amounts.",
            "scen_bull": "Bullish",
            "scen_base": "Central · Reference",
            "scen_bear": "Bearish",
            "scen_probability_label": "Probability",
            "scen_confidence_label": "Confidence {v}",
            "scen_value_scale_note": "Common scale: target bars proportional to the estimated multiple across bull/base/bear.",
            "scenarios_section": "Scenarios",
            "methodology_section": "Methodology & sources",
            "methodology_principle": "Methodological principle: no missing data is ever estimated.",
            "methodology_sources": (
                ("DexScreener", "market & liquidity"),
                ("Blockscout Base", "on-chain, holders, audit"),
                ("CoinGecko", "market cap, FDV, supply"),
                ("Smart money", "proprietary heuristic"),
                ("Report generation", "ARIA engine · anti-hallucination control"),
            ),
            "gaps_title": "Insufficient data: not estimated",
            "gaps_footer": "In line with our principle: no missing data is ever estimated.",
            "fallback_title": "Qualitative LLM analysis unavailable",
            "fallback_body": "This report relies solely on quantitative signals.",
            "ta_section": "Technical analysis",
            "ta_ohlcv_real": "Real OHLCV",
            "ta_trend_prefix": "trend",
            "ta_candles_tf": "{tf} candles",
            "ta_candles_default": "OHLCV candles",
            "ta_caption_note": "levels derived from data, never fabricated",
            "ta_chart_alt": "{cap} chart with derived levels",
            "roi_section": "Comparable-based projection",
            "roi_basis_fdv": "FDV",
            "roi_basis_mcap": "market cap",
            "roi_sector_line": "Sector: {sector}",
            "roi_sector_unknown": "Sector not recognized: generic comparables",
            "roi_ref_label": "Reference {basis} {ref}",
            "roi_disclaimer_default": "Historical placement by comparables — not a forecast nor a target.",
            "market_context_section": "Market context",
            "market_context_btc_line": "Bitcoin (macro reference): {phase} phase since {since}, {change:+.0f}% over this phase ({cycle}).",
            "market_context_disclaimer": "Indicative reading frame (halving-linked cycles): a common model, not a proven market law. No geopolitical or regulatory data included yet.",
            "equities_context_section": "Equities and commodities",
            "equities_context_spy_line": "S&P 500 (SPY proxy ETF): ${price:.2f} ({change:+.2f}% on {date}).",
            "equities_context_qqq_line": "Nasdaq 100 (QQQ proxy ETF): ${price:.2f} ({change:+.2f}% on {date}).",
            "equities_context_commodities_line": "Commodities composite index: {value} {unit} as of {date}.",
            "equities_context_disclaimer": "Proxy ETF, not the native index (this provider has no dedicated index endpoint). Precious metals (gold/silver) not covered (no reliable source wired). Value may be cached (refreshed at most once per 24h, provider cap).",
            "tldr_section": "At a glance",
            "these_section": "Investment thesis",
            "watermark_label": "Personal edition",
            "watermark_personal": "Personal edition · {recipient} · {ref}",
            "detailed_section": "Detailed analysis",
            "standard_teaser": "Detailed analysis, methodology and sources: reserved for the Premium edition.",
            "vanguard_zhc_kicker": "Vanguard · ZHC",
            "footer_ref_label": "REF.",
            "footer_dated": "Dated {date} · SHA-256 fingerprint: {hash}",
            "footer_disclaimer_bold": "proposal subject to human validation",
            "footer_disclaimer": (
                "This note is a {bold}: no automatic execution is ever triggered. "
                "It does not constitute investment advice. Crypto-assets carry a risk "
                "of total loss of invested capital."
            ),
            "microprint_hero": "ARIA Vanguard ZHC · Certified note {ref} · Reproduction prohibited",
            "microprint_footer": (
                "ARIA Vanguard ZHC · Confidential document · {ref} · Reproduction and resale prohibited"
            ),
            "copyright": (
                "© 2026 ARIA Vanguard ZHC · All rights reserved · Confidential document: "
                "reproduction and resale prohibited."
            ),
            "subject_analysis": "VC Analysis",
            "teaser_heading": "Analysis ready",
            "teaser_attached_note": (
                "Full analysis attached (secured PDF). Reproduction and resale prohibited — "
                "strictly personal document."
            ),
        }
    return {
        "tier_premium_label": "RAPPORT PREMIUM",
        "tier_standard_label": "RAPPORT STANDARD",
        "html_title": "ARIA Vanguard ZHC · Note de recherche · {title}",
        "confidential": "Confidentiel",
        "research_note_kicker": "Note de recherche · Analyse d'investissement",
        "network_label": "RÉSEAU BASE",
        "meta_series": "Série {n}",
        "meta_report_num": "Rapport n°{n}",
        "meta_generated": "Généré le {date}",
        "meta_issued_by": "Émis par ARIA Vanguard ZHC",
        "preheader_suffix": "Note de recherche ARIA Vanguard ZHC",
        "refs_none": "Aucun lien officiel disponible pour ce token.",
        "refs_title": "Références : vérifiez par vous-même",
        "refs_disclaimer": "Liens déclarés par le projet (source : DexScreener). Non vérifiés par ARIA.",
        "potential_label": "Potentiel {n}/10",
        "potential_na": "Potentiel n/a",
        "confidence_prefix": "Confiance {v}",
        "risk_prefix": "Risque {v}",
        "rr_qualifier_strong": "Asymétrie très favorable",
        "rr_qualifier_good": "Asymétrie favorable",
        "rr_qualifier_balanced": "Asymétrie équilibrée",
        "rr_qualifier_weak": "Asymétrie défavorable",
        "rr_caption": (
            "{qualifier} : viser +{upside:.0f}% pour {downside:.0f}% risqué. "
            "Rapport des distances (récompense sur risque), pas un multiple de gain."
        ),
        "rr_tight_stop": " Stop serré ({downside:.0f}%) : ratio flatté, facile à toucher.",
        "rr_upside_label": "Potentiel haussier",
        "rr_ratio_label": "Ratio récompense-risque",
        "rr_downside_label": "Risque baissier",
        "order_section": "Ordre proposé",
        "order_reco": "Recommandation",
        "order_size": "Taille suggérée",
        "order_size_value": "{pct:.1f}% du capital",
        "order_capital_to_position": "Capital client → position",
        "order_entry": "Entrée",
        "order_invalidation": "Invalidation",
        "order_target": "Cible",
        "order_disclaimer": "Proposition soumise à validation humaine avant toute exécution.",
        "dollar_section": "Potentiel en $",
        "dollar_position_line": "Position {position} → {target} à la cible",
        "dollar_gain_label": "Gain",
        "dollar_risk_line": "Risque encaissé si invalidation ({inval})",
        "dollar_scale_note": "Échelle commune : barres proportionnelles aux montants en $.",
        "scen_bull": "Haussier",
        "scen_base": "Central · Référence",
        "scen_bear": "Baissier",
        "scen_probability_label": "Probabilité",
        "scen_confidence_label": "Confiance {v}",
        "scen_value_scale_note": "Échelle commune : barres de cible proportionnelles au multiple estimé entre les scénarios haussier/central/baissier.",
        "scenarios_section": "Scénarios",
        "methodology_section": "Méthodologie & sources",
        "methodology_principle": "Principe méthodologique : aucune donnée absente n'est estimée.",
        "methodology_sources": (
            ("DexScreener", "marché & liquidité"),
            ("Blockscout Base", "on-chain, holders, audit"),
            ("CoinGecko", "market cap, FDV, supply"),
            ("Smart-money", "heuristique propriétaire"),
            ("Rédaction", "moteur ARIA · contrôle anti-hallucination"),
        ),
        "gaps_title": "Données insuffisantes : non estimées",
        "gaps_footer": "Conformément à notre principe : aucune donnée absente n'est estimée.",
        "fallback_title": "Analyse qualitative LLM indisponible",
        "fallback_body": "Ce rapport repose uniquement sur les signaux quantitatifs.",
        "ta_section": "Analyse technique",
        "ta_ohlcv_real": "OHLCV réel",
        "ta_trend_prefix": "tendance",
        "ta_candles_tf": "Bougies {tf}",
        "ta_candles_default": "Bougies OHLCV",
        "ta_caption_note": "niveaux dérivés des données, jamais fabriqués",
        "ta_chart_alt": "Graphique {cap} avec niveaux dérivés",
        "roi_section": "Projection par comparables",
        "roi_basis_fdv": "FDV",
        "roi_basis_mcap": "capitalisation",
        "roi_sector_line": "Secteur : {sector}",
        "roi_sector_unknown": "Secteur non reconnu : comparables génériques",
        "roi_ref_label": "{basis} de référence {ref}",
        "roi_disclaimer_default": "Placement historique par comparables, pas une prevision ni une cible.",
        "market_context_section": "Contexte marché",
        "market_context_btc_line": "Bitcoin (référence macro) : phase de {phase} depuis le {since}, {change:+.0f}% sur cette phase ({cycle}).",
        "market_context_disclaimer": "Cadre de lecture indicatif (cycles liés au halving) : un modèle répandu, pas une loi de marché prouvée. Aucune donnée géopolitique ou réglementaire intégrée pour l'instant.",
        "equities_context_section": "Actions et matières premières",
        "equities_context_spy_line": "S&P 500 (proxy ETF SPY) : {price:.2f}$ ({change:+.2f}% au {date}).",
        "equities_context_qqq_line": "Nasdaq 100 (proxy ETF QQQ) : {price:.2f}$ ({change:+.2f}% au {date}).",
        "equities_context_commodities_line": "Indice composite matières premières : {value} {unit} au {date}.",
        "equities_context_disclaimer": "Proxy ETF, pas l'indice natif (ce fournisseur n'a pas d'endpoint indice dédié). Métaux précieux (or/argent) non couverts (aucune source fiable branchée). Valeur potentiellement en cache (rafraîchie au plus une fois par 24h, plafond fournisseur).",
        "tldr_section": "En bref",
        "these_section": "Thèse d'investissement",
        "watermark_label": "Édition personnelle",
        "watermark_personal": "Édition personnelle · {recipient} · {ref}",
        "detailed_section": "Analyse détaillée",
        "standard_teaser": "Analyse détaillée, méthodologie et sources : réservées à l'édition Premium.",
        "vanguard_zhc_kicker": "Vanguard · ZHC",
        "footer_ref_label": "RÉF.",
        "footer_dated": "Daté du {date} · Empreinte SHA-256 : {hash}",
        "footer_disclaimer_bold": "proposition soumise à validation humaine",
        "footer_disclaimer": (
            "Cette note constitue une {bold} : aucune exécution automatique n'est "
            "engagée. Elle ne constitue pas un conseil en investissement. Les "
            "crypto-actifs présentent un risque de perte totale du capital investi."
        ),
        "microprint_hero": "ARIA Vanguard ZHC · Note certifiée {ref} · Reproduction interdite",
        "microprint_footer": (
            "ARIA Vanguard ZHC · Document confidentiel · {ref} · Reproduction et revente interdites"
        ),
        "copyright": (
            "© 2026 ARIA Vanguard ZHC · Tous droits réservés · Document confidentiel : "
            "reproduction et revente interdites."
        ),
        "subject_analysis": "Analyse VC",
        "teaser_heading": "Analyse prête",
        "teaser_attached_note": (
            "Analyse complète en pièce jointe (PDF sécurisé). Reproduction et revente "
            "interdites — document strictement personnel."
        ),
    }


# --- Judge verdict labels (proof engine, test mode) ------------------------------
def judge_strings(lang: str) -> dict:
    if norm_lang(lang) == LANG_EN:
        return {
            "header": "🧑‍⚖️ TEST MODE — Proof-engine audit ({src})",
            "src_llm": "LLM judge",
            "src_det": "deterministic judge (LLM unavailable)",
            "verdict": "Verdict",
            "score": "Score",
            "rr_ok": "R/R coherent",
            "yes": "yes",
            "no": "no",
            "reco": "Judge recommendation",
            "strengths": "✅ Strengths:",
            "weaknesses": "⚠️ Weaknesses:",
            "unsupported": "🚩 Unsupported claims:",
        }
    return {
        "header": "🧑‍⚖️ MODE TEST — Audit du proof engine ({src})",
        "src_llm": "juge LLM",
        "src_det": "juge déterministe (LLM indispo)",
        "verdict": "Verdict",
        "score": "Score",
        "rr_ok": "R/R cohérent",
        "yes": "oui",
        "no": "non",
        "reco": "Recommandation du juge",
        "strengths": "✅ Points forts :",
        "weaknesses": "⚠️ Points faibles :",
        "unsupported": "🚩 Affirmations non étayées :",
    }
