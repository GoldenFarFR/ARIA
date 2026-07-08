"""Internationalisation de la surface VC — FR par défaut, EN additif.

Principe de sûreté : le **français reste le comportement historique validé**
(sortie byte-identique à avant). L'anglais est **purement additif** — seuls
changent (a) l'affichage des libellés fixes et (b) une directive de langue
ajoutée au prompt LLM. Les **chiffres, scores, adresses et codes d'enum**
(`risque`, `confiance`, `recommandation`) restent identiques : jamais de
ré-interprétation, seulement une traduction de la prose.
"""
from __future__ import annotations

from aria_core.locale import LANG_EN, LANG_FR

SUPPORTED_VC_LANGS = (LANG_FR, LANG_EN)


def norm_lang(lang: str | None) -> str:
    """Normalise vers une langue supportée ; défaut FR (jamais d'erreur)."""
    value = (lang or "").strip().lower()
    return value if value in SUPPORTED_VC_LANGS else LANG_FR


# --- Directive de langue pour le LLM (analyse + juge) --------------------------
# Ajoutée UNIQUEMENT en anglais. En FR le prompt reste inchangé (chaîne vide).
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
    """Suffixe à concaténer au prompt système. Vide en FR (aucune régression)."""
    return _LLM_DIRECTIVE_EN if norm_lang(lang) == LANG_EN else ""


# --- Traduction des codes de risque pour l'affichage ---------------------------
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
    """Traduit le code de risque pour l'affichage (identité en FR)."""
    if norm_lang(lang) == LANG_EN:
        return _RISK_EN.get((risque or "").strip().upper(), risque)
    return risque


# --- Libellés de l'ordre Telegram (format_telegram_order) ----------------------
def order_strings(lang: str) -> dict:
    """Libellés fixes de l'ordre Telegram. FR = exactement l'existant."""
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


# --- Messages d'ossature du flux /vc (Telegram) --------------------------------
def scaffold_strings(lang: str) -> dict:
    """Messages fixes du handler /vc (statut, mode test, usage)."""
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


# --- Libellés du verdict du juge (proof engine, mode test) ---------------------
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
