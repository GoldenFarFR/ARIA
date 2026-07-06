"""Rendu du rapport VC HTML — Étape C (rendu).

Vérifie la présence des sections, l'échappement HTML de tout contenu dynamique
(défense injection = extension du dôme), et le mini-markdown.
"""
from __future__ import annotations

from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_report import (
    email_subject,
    render_html_report,
    report_integrity,
    _render_markdown_body,
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
    )
    base.update(kw)
    return VCResult(**base)


def test_report_contains_core_sections():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "ARIA Vanguard ZHC" in out
    assert "Potentiel" in out
    assert "Recommandation" in out
    assert "Thèse" in out
    assert "Ordre proposé" in out
    assert "Données insuffisantes" in out
    assert "proposition soumise à validation humaine" in out
    assert _GEN in out


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
    # Les balises hostiles sont échappées, jamais actives.
    assert "<script>" not in out
    assert "alert('xss')" not in out or "&lt;script&gt;" in out
    assert "&lt;script&gt;" in out
    assert "onerror=alert" not in out.replace("&lt;", "<").replace("&gt;", ">").replace("&#x27;", "'") or "&lt;img" in out
    assert "&lt;img" in out


def test_report_no_order_block_when_not_actionable():
    out = render_html_report(_result(recommandation="WATCH", taille_pct=0.0), generated_at=_GEN)
    assert "Ordre proposé" not in out


def test_report_fallback_note_when_llm_disabled():
    out = render_html_report(
        _result(llm_used=False, potentiel=None, recommandation="WATCH", taille_pct=0.0),
        generated_at=_GEN,
    )
    assert "Analyse qualitative LLM indisponible" in out
    assert "n/a" in out


def test_email_subject_format():
    subj = email_subject(_result())
    assert "ARIA Vanguard ZHC" in subj
    assert "BUY" in subj
    assert "7/10" in subj


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
    assert "Édition personnelle de" in out
    assert "agentaria.zhc@gmail.com" in out


def test_report_no_watermark_without_recipient():
    out = render_html_report(_result(), generated_at=_GEN)
    assert "Édition personnelle de" not in out


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
