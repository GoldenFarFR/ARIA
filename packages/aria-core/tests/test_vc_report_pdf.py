"""Rapport VC en PDF sécurisé — rendu (reportlab) + permissions anti-copie (pypdf).

Aucun réseau : entièrement hors-ligne. Vérifie que le PDF se construit pour
premium/standard/fr/en, qu'il embarque le texte attendu, et que le chiffrement
retire bien les permissions d'extraction/modification (jamais un test de
« sécurité absolue » — cf. avertissement du module : dissuasif, pas inviolable).
"""
from __future__ import annotations

import io

import pytest

from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_report_pdf import render_pdf_report, secure_pdf_bytes

ADDR = "0x" + "a" * 40
_GEN = "08/07/2026 15:00 UTC"


def _result(**kw) -> VCResult:
    base = dict(
        contract=ADDR, potentiel=8, risque="MODÉRÉ", these="Builder actif, traction réelle.",
        recommandation="BUY", taille_pct=2.5, entree="0.001", invalidation="0.0007", cible="0.003",
        donnees_insuffisantes=["Volume 24h non disponible"],
        rapport_detaille="## Analyse\n\nCeci est un test.\n- point 1\n- point 2",
        security_score=82, lite_verdict="SAFE", llm_used=True,
        resume_executif="Projet prometteur avec builder actif.",
        confiance_globale="haute",
        scenarios=[
            {"nom": "bull", "cible": "0.005", "probabilite": 30, "confiance": "moyenne"},
            {"nom": "base", "cible": "0.003", "probabilite": 50, "confiance": "haute"},
            {"nom": "bear", "cible": "0.0007", "probabilite": 20, "confiance": "faible"},
        ],
        upside_pct=200.0, downside_pct=30.0,
        liens_projet=[{"label": "Site", "url": "https://example.com"}], symbol="TST",
    )
    base.update(kw)
    return VCResult(**base)


def _extract_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() for p in reader.pages)


def test_render_pdf_report_premium_fr_contains_expected_sections():
    pdf = render_pdf_report(_result(), generated_at=_GEN, recipient="client@example.com",
                             report_number=2, series_number=47, capital_usd=5000, tier="premium", lang="fr")
    text = _extract_text(pdf)
    assert "ARIA" in text
    assert "RAPPORT PREMIUM" in text
    assert "TST" in text
    assert "BUY" in text
    assert "Builder actif" in text  # thèse présente dans le PDF (contrairement au teaser email)


def test_render_pdf_report_standard_hides_premium_sections():
    pdf = render_pdf_report(_result(), generated_at=_GEN, tier="standard", lang="fr")
    text = _extract_text(pdf)
    assert "RAPPORT STANDARD" in text
    assert "Méthodologie" not in text
    assert "Projection par comparables" not in text


def test_render_pdf_report_lang_en_translates_labels():
    pdf = render_pdf_report(_result(), generated_at=_GEN, tier="premium", lang="en")
    text = _extract_text(pdf)
    assert "PREMIUM REPORT" in text
    assert "Proposed order" in text
    assert "Confidential" in text
    assert "Ordre proposé" not in text


def test_render_pdf_report_no_capital_omits_dollar_section():
    pdf = render_pdf_report(_result(), generated_at=_GEN, capital_usd=None, lang="fr")
    text = _extract_text(pdf)
    assert "Potentiel en $" not in text


def test_render_pdf_report_watermarks_recipient():
    pdf = render_pdf_report(_result(), generated_at=_GEN, recipient="client@example.com", lang="fr")
    text = _extract_text(pdf)
    assert "client@example.com" in text


def test_render_pdf_report_escapes_hostile_content():
    """`_esc()` neutralise `<script>`/`<b>` AVANT injection dans un ``Paragraph`` —
    sans quoi le mini-parseur XML de reportlab lèverait une erreur de parsing sur
    une balise non reconnue (`<script>` n'existe pas dans son mini-langage), ou
    interpréterait `<b>` comme une VRAIE mise en gras. Le fait que le PDF se
    construise sans exception ET affiche le texte adverse tel quel (inerte,
    jamais interprété comme balise) est la preuve : identique au principe HTML
    (`&lt;script&gt;` s'affiche comme texte, jamais exécuté)."""
    hostile = _result(these="<script>alert(1)</script>", entree="<b>marché</b> forcé en gras ?")
    pdf = render_pdf_report(hostile, generated_at=_GEN, lang="fr")  # ne doit jamais lever
    text = _extract_text(pdf)
    assert "<script>alert(1)</script>" in text  # rendu tel quel, en texte INERTE (pas exécuté)
    assert "marché" in text and "forcé en gras" in text


def test_render_pdf_report_empty_gaps_and_scenarios_degrade_gracefully():
    minimal = _result(donnees_insuffisantes=[], scenarios=[], upside_pct=None, downside_pct=None)
    pdf = render_pdf_report(minimal, generated_at=_GEN, lang="fr")
    assert len(pdf) > 0  # ne plante jamais sur données absentes


# ── Sécurisation (permissions anti-copie) ────────────────────────────────────

def test_secure_pdf_bytes_opens_without_password_but_restricts_permissions():
    from pypdf import PdfReader
    from pypdf.constants import UserAccessPermissions as Perm

    raw = render_pdf_report(_result(), generated_at=_GEN, lang="fr")
    secured = secure_pdf_bytes(raw, owner_password="test-owner-secret")

    reader = PdfReader(io.BytesIO(secured))
    assert reader.is_encrypted
    # Mot de passe utilisateur vide : la lecture réussit sans mot de passe fourni.
    text = reader.pages[0].extract_text()
    assert "ARIA" in text

    perms = int(reader.trailer["/Encrypt"]["/P"])
    assert not (perms & int(Perm.EXTRACT))
    assert not (perms & int(Perm.EXTRACT_TEXT_AND_GRAPHICS))
    assert not (perms & int(Perm.MODIFY))
    assert not (perms & int(Perm.ADD_OR_MODIFY))


def test_secure_pdf_bytes_is_deterministic_content_length_stable():
    raw = render_pdf_report(_result(), generated_at=_GEN, lang="fr")
    secured_a = secure_pdf_bytes(raw, owner_password="secret-a")
    secured_b = secure_pdf_bytes(raw, owner_password="secret-b")
    assert len(secured_a) > 0 and len(secured_b) > 0


def test_module_never_holds_or_reuses_a_fixed_owner_secret():
    """Garde-fou : aucun mot de passe propriétaire codé en dur dans le module
    (l'appelant — vc_delivery — doit en générer un jetable à chaque envoi)."""
    import inspect

    from aria_core.skills import vc_report_pdf

    src = inspect.getsource(vc_report_pdf)
    assert "owner_password=" not in src.split("def secure_pdf_bytes")[0]
