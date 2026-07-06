"""Rendu du rapport VC en HTML « qualité institutionnelle » (Étape C — rendu).

Transforme un ``VCResult`` en document HTML autonome (CSS inline, aucun asset
externe) destiné à l'email ARIA — et, plus tard, au site / à l'abonnement.

## Sécurité (extension du dôme à la couche rendu)

Le contenu du rapport est produit par le LLM, qui a lui-même traité des données
non fiables (nom du token, catégories, etc.). **Tout champ dynamique est
HTML-échappé** (`html.escape`) avant d'être injecté dans le template. Le
mini-rendu markdown n'émet que des balises de mise en forme contrôlées
(`h2/h3/h4`, `p`, `ul/li`, `strong`) — jamais de HTML fourni par le modèle. Cela
neutralise toute tentative d'injection (email ou, demain, page web publique).
"""
from __future__ import annotations

import hashlib
import html
import re

from aria_core.skills.vc_analysis import VCResult

BRAND = "ARIA Vanguard ZHC"
_ACCENT = "#0b1f3a"  # navy institutionnel
_ACCENT_SOFT = "#12325c"

# Couleurs des badges par recommandation / risque (contrôlées, jamais du LLM).
_RECO_COLORS = {
    "BUY": "#0a7d3c",
    "WATCH": "#8a6d00",
    "SELL": "#a12622",
    "AVOID": "#5a5a5a",
}
_RISK_COLORS = {
    "FAIBLE": "#0a7d3c",
    "MODÉRÉ": "#8a6d00",
    "ÉLEVÉ": "#a15a00",
    "EXTRÊME": "#a12622",
}


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _render_markdown_body(text: str) -> str:
    """Mini-markdown → HTML, tout échappé. Supporte titres, listes, gras, paragraphes."""
    if not text:
        return "<p style='color:#666'>Aucun contenu.</p>"

    html_parts: list[str] = []
    list_open = False

    def _inline(raw: str) -> str:
        # Échappe d'abord, puis applique le gras sur le texte déjà sûr.
        safe = _esc(raw)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)

    def _close_list() -> None:
        nonlocal list_open
        if list_open:
            html_parts.append("</ul>")
            list_open = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            _close_list()
            continue
        if stripped.startswith("### "):
            _close_list()
            html_parts.append(
                f"<h4 style='margin:18px 0 6px;color:{_ACCENT};font-size:15px'>{_inline(stripped[4:])}</h4>"
            )
        elif stripped.startswith("## "):
            _close_list()
            html_parts.append(
                f"<h3 style='margin:22px 0 8px;color:{_ACCENT};font-size:17px;"
                f"border-bottom:2px solid #e6ebf2;padding-bottom:4px'>{_inline(stripped[3:])}</h3>"
            )
        elif stripped.startswith("# "):
            _close_list()
            html_parts.append(
                f"<h2 style='margin:24px 0 10px;color:{_ACCENT};font-size:19px'>{_inline(stripped[2:])}</h2>"
            )
        elif stripped[:2] in ("- ", "* "):
            if not list_open:
                html_parts.append("<ul style='margin:6px 0 6px 20px;padding:0'>")
                list_open = True
            html_parts.append(f"<li style='margin:3px 0;line-height:1.5'>{_inline(stripped[2:])}</li>")
        else:
            _close_list()
            html_parts.append(f"<p style='margin:8px 0;line-height:1.6;color:#1c2530'>{_inline(stripped)}</p>")

    _close_list()
    return "\n".join(html_parts)


def _badge(label: str, value: str, color: str) -> str:
    return (
        "<td style='padding:0 8px 0 0'>"
        "<div style='background:#f4f7fb;border:1px solid #e6ebf2;border-radius:8px;"
        "padding:12px 14px;text-align:center'>"
        f"<div style='font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#6b7684'>{_esc(label)}</div>"
        f"<div style='font-size:20px;font-weight:700;margin-top:4px;color:{color}'>{_esc(value)}</div>"
        "</div></td>"
    )


def report_integrity(result: VCResult, *, generated_at: str, recipient: str | None = None) -> tuple[str, str]:
    """Empreinte du rapport : (référence courte, SHA-256 complet).

    Déterministe sur le contenu + destinataire + horodatage. La référence courte
    identifie l'édition ; le SHA-256 permet de prouver qu'un rapport n'a pas été
    falsifié (anti-alteration) et, combiné au destinataire, de tracer une fuite.
    """
    basis = "|".join(
        [
            str(result.contract),
            str(generated_at),
            recipient or "",
            str(result.recommandation),
            str(result.potentiel),
            str(result.rapport_detaille),
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return digest[:12].upper(), digest


def email_subject(result: VCResult) -> str:
    """Objet d'email concis et professionnel (échappé côté rendu HTML, brut ici pour l'en-tête)."""
    potentiel = f"{result.potentiel}/10" if result.potentiel is not None else "n/a"
    return f"[{BRAND}] Analyse VC — {result.recommandation} · Potentiel {potentiel} · {result.contract[:10]}…"


def render_html_report(result: VCResult, *, generated_at: str, recipient: str | None = None) -> str:
    """Document HTML autonome, CSS inline — prêt pour l'email (et le futur site).

    ``recipient`` (optionnel) inscrit un filigrane d'édition personnelle : une
    fuite du rapport devient traçable au destinataire. Une empreinte SHA-256 du
    contenu est apposée en pied (anti-falsification).
    """
    potentiel = f"{result.potentiel}/10" if result.potentiel is not None else "n/a"
    reco_color = _RECO_COLORS.get(result.recommandation, "#5a5a5a")
    risk_color = _RISK_COLORS.get(result.risque, "#5a5a5a")
    ref_id, full_hash = report_integrity(result, generated_at=generated_at, recipient=recipient)

    # Encart ordre proposé (seulement si actionnable).
    order_block = ""
    if result.actionable:
        rows = [("Recommandation", result.recommandation)]
        if result.recommandation == "BUY":
            rows.append(("Taille suggérée", f"{result.taille_pct:.1f}% du capital"))
        rows += [
            ("Entrée", result.entree),
            ("Invalidation", result.invalidation),
            ("Cible", result.cible),
        ]
        order_rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:#6b7684;font-size:13px'>{_esc(k)}</td>"
            f"<td style='padding:4px 0;font-weight:600;color:#1c2530;font-size:13px'>{_esc(v)}</td></tr>"
            for k, v in rows
        )
        order_block = (
            f"<div style='background:#f4f7fb;border-left:4px solid {reco_color};border-radius:6px;"
            "padding:14px 18px;margin:18px 0'>"
            f"<div style='font-weight:700;color:{_ACCENT};margin-bottom:8px'>Ordre proposé</div>"
            f"<table style='border-collapse:collapse'>{order_rows}</table></div>"
        )

    # Section données insuffisantes (honnêteté / anti-hallucination).
    gaps_block = ""
    if result.donnees_insuffisantes:
        items = "".join(f"<li style='margin:2px 0'>{_esc(g)}</li>" for g in result.donnees_insuffisantes)
        gaps_block = (
            "<div style='background:#fff8e6;border:1px solid #f0e0a8;border-radius:6px;"
            "padding:12px 16px;margin:18px 0'>"
            "<div style='font-weight:600;color:#8a6d00;margin-bottom:6px'>Données insuffisantes (non estimées)</div>"
            f"<ul style='margin:0 0 0 18px;padding:0;color:#5c4d00;font-size:13px'>{items}</ul></div>"
        )

    fallback_note = ""
    if not result.llm_used:
        fallback_note = (
            "<div style='background:#fdecec;border:1px solid #f0b8b8;border-radius:6px;"
            "padding:12px 16px;margin:18px 0;color:#a12622;font-size:13px'>"
            "Analyse qualitative LLM indisponible — ce rapport repose uniquement sur les signaux quantitatifs.</div>"
        )

    # Filigrane d'édition personnelle (traçabilité des fuites) — seulement si destinataire connu.
    watermark_line = ""
    if recipient:
        watermark_line = (
            f"<div style='margin-top:4px'>Édition personnelle de "
            f"<strong>{_esc(recipient)}</strong> — {_esc(generated_at)}. "
            "Toute diffusion engage la responsabilité du destinataire.</div>"
        )

    body_html = _render_markdown_body(result.rapport_detaille)

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#eef1f6;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" style="border-collapse:collapse;background:#eef1f6"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="640" style="max-width:640px;width:100%;border-collapse:collapse;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(11,31,58,.08)">
  <tr><td style="background:linear-gradient(135deg,{_ACCENT},{_ACCENT_SOFT});padding:24px 28px">
    <div style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:.02em">{_esc(BRAND)}</div>
    <div style="color:#b9c8de;font-size:13px;margin-top:2px">Analyse d'investissement — note de recherche</div>
  </td></tr>
  <tr><td style="padding:22px 28px 6px">
    <div style="font-size:12px;color:#6b7684">Token</div>
    <div style="font-size:15px;font-weight:600;color:#1c2530;word-break:break-all">{_esc(result.contract)}</div>
    <div style="font-size:12px;color:#9aa4b1;margin-top:4px">Généré le {_esc(generated_at)}</div>
  </td></tr>
  <tr><td style="padding:14px 28px 0">
    <table role="presentation" width="100%" style="border-collapse:collapse"><tr>
      {_badge("Potentiel", potentiel, _ACCENT)}
      {_badge("Risque", result.risque, risk_color)}
      {_badge("Recommandation", result.recommandation, reco_color)}
    </tr></table>
  </td></tr>
  <tr><td style="padding:6px 28px">
    {fallback_note}
    <div style="background:#f7f9fc;border-radius:8px;padding:14px 18px;margin:14px 0">
      <div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b7684;margin-bottom:4px">Thèse</div>
      <div style="font-size:14px;line-height:1.6;color:#1c2530">{_esc(result.these)}</div>
    </div>
    {order_block}
    {gaps_block}
    <div style="margin-top:10px">{body_html}</div>
  </td></tr>
  <tr><td style="padding:18px 28px 26px;border-top:1px solid #eef1f6">
    <div style="font-size:11px;line-height:1.6;color:#9aa4b1">
      Ce document est une note de recherche automatisée produite par ARIA. Il constitue une
      <strong>proposition soumise à validation humaine</strong> — jamais un ordre d'exécution automatique.
      Aucune exécution n'est réalisée par ARIA ; toute position est signée manuellement par l'opérateur.
      Ce n'est pas un conseil en investissement.
    </div>
    <div style="margin-top:12px;font-size:10px;line-height:1.7;color:#b3bcc8">
      © 2026 {_esc(BRAND)} — Tous droits réservés. Document <strong>confidentiel</strong> :
      reproduction, rediffusion ou revente interdites sans autorisation écrite.
      {watermark_line}
      <div style="margin-top:4px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#c2c9d4">
        Réf. {_esc(ref_id)} · Empreinte SHA-256 : {_esc(full_hash)}
      </div>
    </div>
  </td></tr>
</table></td></tr></table></body></html>"""
