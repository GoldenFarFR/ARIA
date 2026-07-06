"""Rendu du rapport VC en HTML « qualité institutionnelle » (Étape C — rendu).

Transforme un ``VCResult`` en document HTML autonome (CSS inline, emblème embarqué
en base64 — aucun asset externe requis à l'affichage) destiné à l'email ARIA — et,
plus tard, au site / à l'abonnement.

## Sécurité (extension du dôme à la couche rendu)

Le contenu du rapport est produit par le LLM, qui a lui-même traité des données
non fiables (nom du token, catégories, etc.). **Tout champ dynamique est
HTML-échappé** (`html.escape`) avant d'être injecté dans le template. Le
mini-rendu markdown n'émet que des balises de mise en forme contrôlées
(`h2/h3/h4`, `p`, `ul/li`, `strong`) — jamais de HTML fourni par le modèle. Cela
neutralise toute tentative d'injection (email ou, demain, page web publique).
"""
from __future__ import annotations

import base64
import hashlib
import html
import re
from functools import lru_cache
from pathlib import Path

from aria_core.skills.vc_analysis import VCResult

BRAND = "ARIA Vanguard ZHC"
_ACCENT = "#0b1f3a"       # navy institutionnel
_ACCENT_SOFT = "#12325c"
_GOLD = "#c9a227"         # or — accent luxe
_GOLD_SOFT = "#bfa15a"
_INK = "#1c2530"
_MUTE = "#6b7684"

_EMBLEM_PATH = Path(__file__).resolve().parents[1] / "assets" / "aria_emblem.png"

# Couleurs des badges par recommandation / risque / confiance (contrôlées, jamais du LLM).
_RECO_COLORS = {"BUY": "#0a7d3c", "WATCH": "#8a6d00", "SELL": "#a12622", "AVOID": "#5a5a5a"}
_RISK_COLORS = {"FAIBLE": "#0a7d3c", "MODÉRÉ": "#8a6d00", "ÉLEVÉ": "#a15a00", "EXTRÊME": "#a12622"}
_CONF_COLORS = {"haute": "#0a7d3c", "moyenne": "#8a6d00", "faible": "#8a6d00"}
_SCEN_META = {
    "bull": ("Scénario haussier", "#0a7d3c"),
    "base": ("Scénario central", _ACCENT_SOFT),
    "bear": ("Scénario baissier", "#a12622"),
}


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


@lru_cache(maxsize=1)
def _emblem_data_uri() -> str:
    """Charge l'emblème une fois et le renvoie en data-URI base64 (dégrade en '' si absent)."""
    try:
        raw = _EMBLEM_PATH.read_bytes()
    except OSError:
        return ""
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _render_markdown_body(text: str) -> str:
    """Mini-markdown → HTML, tout échappé. Supporte titres, listes, gras, paragraphes."""
    if not text:
        return "<p style='color:#666'>Aucun contenu.</p>"

    html_parts: list[str] = []
    list_open = False

    def _inline(raw: str) -> str:
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
                f"<h3 style='margin:22px 0 8px;color:{_ACCENT};font-size:17px;font-family:Georgia,serif;"
                f"border-bottom:2px solid {_GOLD};padding-bottom:4px'>{_inline(stripped[3:])}</h3>"
            )
        elif stripped.startswith("# "):
            _close_list()
            html_parts.append(
                f"<h2 style='margin:24px 0 10px;color:{_ACCENT};font-size:19px;font-family:Georgia,serif'>{_inline(stripped[2:])}</h2>"
            )
        elif stripped[:2] in ("- ", "* "):
            if not list_open:
                html_parts.append("<ul style='margin:6px 0 6px 20px;padding:0'>")
                list_open = True
            html_parts.append(f"<li style='margin:3px 0;line-height:1.5'>{_inline(stripped[2:])}</li>")
        else:
            _close_list()
            html_parts.append(f"<p style='margin:8px 0;line-height:1.6;color:{_INK}'>{_inline(stripped)}</p>")

    _close_list()
    return "\n".join(html_parts)


def _badge(label: str, value: str, color: str) -> str:
    return (
        "<td style='padding:0 6px 0 0;width:50%'>"
        "<div style='background:#f4f7fb;border:1px solid #e6ebf2;border-radius:8px;padding:12px 14px;text-align:center'>"
        f"<div style='font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{_MUTE}'>{_esc(label)}</div>"
        f"<div style='font-size:19px;font-weight:700;margin-top:4px;color:{color}'>{_esc(value)}</div>"
        "</div></td>"
    )


def _potentiel_gauge(potentiel: int | None) -> str:
    """Jauge visuelle du Potentiel (0-10) : grand chiffre + barre segmentée or — le point focal."""
    filled = potentiel if isinstance(potentiel, int) else 0
    value_txt = f"{potentiel}" if potentiel is not None else "—"
    segments = "".join(
        f"<td style='padding:0 2px'><div style='height:10px;border-radius:2px;"
        f"background:{_GOLD if i < filled else '#e6ebf2'}'></div></td>"
        for i in range(10)
    )
    return (
        f"<div style='background:linear-gradient(135deg,{_ACCENT},{_ACCENT_SOFT});border-radius:10px;"
        "padding:18px 22px;margin:4px 0 14px'>"
        "<table role='presentation' width='100%' style='border-collapse:collapse'><tr>"
        "<td style='vertical-align:middle'>"
        f"<div style='font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:{_GOLD_SOFT}'>Potentiel VC</div>"
        f"<div style='color:#ffffff;font-family:Georgia,serif'><span style='font-size:44px;font-weight:700'>{_esc(value_txt)}</span>"
        "<span style='font-size:18px;color:#b9c8de'> / 10</span></div>"
        "</td></tr></table>"
        f"<table role='presentation' width='100%' style='border-collapse:collapse;margin-top:10px'><tr>{segments}</tr></table>"
        "</div>"
    )


def _scenarios_block(scenarios: list[dict]) -> str:
    """Trois scénarios chiffrés (bull/base/bear) : cible, probabilité (barre), confiance."""
    if not scenarios:
        return ""
    cells = []
    for sc in scenarios:
        titre, color = _SCEN_META.get(sc.get("nom", ""), ("Scénario", _MUTE))
        proba = sc.get("probabilite", 0)
        conf = sc.get("confiance", "faible")
        cells.append(
            f"<td style='width:33%;vertical-align:top;padding:0 5px'>"
            f"<div style='border:1px solid #e6ebf2;border-top:3px solid {color};border-radius:8px;padding:12px 12px 14px'>"
            f"<div style='font-size:12px;font-weight:700;color:{color}'>{_esc(titre)}</div>"
            f"<div style='font-size:13px;color:{_INK};margin:6px 0;min-height:34px'>{_esc(sc.get('cible'))}</div>"
            f"<div style='font-size:11px;color:{_MUTE}'>Probabilité {_esc(proba)}%</div>"
            f"<div style='background:#eef1f6;border-radius:3px;height:6px;margin-top:3px'>"
            f"<div style='width:{max(0, min(100, proba))}%;height:6px;border-radius:3px;background:{color}'></div></div>"
            f"<div style='font-size:10px;color:{_MUTE};margin-top:6px'>Confiance : {_esc(conf)}</div>"
            "</div></td>"
        )
    return (
        "<div style='margin:18px 0'>"
        f"<div style='font-family:Georgia,serif;font-size:16px;color:{_ACCENT};border-bottom:2px solid {_GOLD};"
        "padding-bottom:4px;margin-bottom:10px'>Scénarios</div>"
        "<table role='presentation' width='100%' style='border-collapse:collapse'><tr>"
        + "".join(cells)
        + "</tr></table></div>"
    )


def _methodology_block(result: VCResult) -> str:
    """Annexe méthodologie & sources — montre le sérieux, distingue vérifié vs supposé."""
    sources = [
        "Marché & liquidité : DexScreener (agrégé Base).",
        "On-chain : Blockscout Base (holders, audit de contrat, transferts).",
        "Fondamentaux : CoinGecko (market cap, FDV, supply, catégories).",
        "Smart-money : heuristique propriétaire (comportement mesurable, pas identité).",
        "Rédaction : modèle Claude Opus via Spark (analyse), sous contrôle anti-hallucination.",
    ]
    src_items = "".join(f"<li style='margin:3px 0'>{_esc(s)}</li>" for s in sources)
    gaps = ""
    if result.donnees_insuffisantes:
        gi = "".join(f"<li style='margin:2px 0'>{_esc(g)}</li>" for g in result.donnees_insuffisantes)
        gaps = (
            f"<div style='margin-top:8px;font-size:12px;color:{_MUTE}'>Éléments non sourçables, "
            f"volontairement non estimés :</div><ul style='margin:2px 0 0 18px;padding:0;font-size:12px;color:{_MUTE}'>{gi}</ul>"
        )
    return (
        "<div style='margin:20px 0 4px;background:#f7f9fc;border-radius:8px;padding:14px 18px'>"
        f"<div style='font-family:Georgia,serif;font-size:15px;color:{_ACCENT};margin-bottom:6px'>Méthodologie & sources</div>"
        f"<ul style='margin:0 0 0 18px;padding:0;font-size:12px;color:{_INK}'>{src_items}</ul>"
        f"{gaps}"
        f"<div style='margin-top:8px;font-size:11px;color:{_MUTE}'>Principe : aucune donnée absente n'est estimée — "
        "elle est signalée comme « donnée insuffisante ». Ce qui est vérifié est distingué de ce qui est supposé.</div>"
        "</div>"
    )


def report_integrity(result: VCResult, *, generated_at: str, recipient: str | None = None) -> tuple[str, str]:
    """Empreinte du rapport : (référence courte, SHA-256 complet). Déterministe."""
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
    """Objet d'email concis et professionnel."""
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
    conf_color = _CONF_COLORS.get(result.confiance_globale, _MUTE)
    ref_id, full_hash = report_integrity(result, generated_at=generated_at, recipient=recipient)

    emblem = _emblem_data_uri()
    emblem_img = (
        f"<img src='{emblem}' width='52' height='52' alt='' style='display:block'>" if emblem else ""
    )

    # TL;DR (résumé exécutif) — l'accroche.
    tldr_block = ""
    if result.resume_executif:
        tldr_block = (
            f"<div style='background:#fbf7ea;border-left:4px solid {_GOLD};border-radius:6px;padding:14px 18px;margin:16px 0'>"
            f"<div style='font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:{_GOLD_SOFT};margin-bottom:4px'>En bref</div>"
            f"<div style='font-size:15px;line-height:1.6;color:{_INK}'>{_esc(result.resume_executif)}</div></div>"
        )

    # Ordre proposé (si actionnable).
    order_block = ""
    if result.actionable:
        rows = [("Recommandation", result.recommandation)]
        if result.recommandation == "BUY":
            rows.append(("Taille suggérée", f"{result.taille_pct:.1f}% du capital"))
        rows += [("Entrée", result.entree), ("Invalidation", result.invalidation), ("Cible", result.cible)]
        order_rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:{_MUTE};font-size:13px'>{_esc(k)}</td>"
            f"<td style='padding:4px 0;font-weight:600;color:{_INK};font-size:13px'>{_esc(v)}</td></tr>"
            for k, v in rows
        )
        order_block = (
            f"<div style='background:#f4f7fb;border-left:4px solid {reco_color};border-radius:6px;padding:14px 18px;margin:18px 0'>"
            f"<div style='font-weight:700;color:{_ACCENT};margin-bottom:8px'>Ordre proposé</div>"
            f"<table style='border-collapse:collapse'>{order_rows}</table></div>"
        )

    gaps_block = ""
    if result.donnees_insuffisantes:
        items = "".join(f"<li style='margin:2px 0'>{_esc(g)}</li>" for g in result.donnees_insuffisantes)
        gaps_block = (
            "<div style='background:#fff8e6;border:1px solid #f0e0a8;border-radius:6px;padding:12px 16px;margin:18px 0'>"
            "<div style='font-weight:600;color:#8a6d00;margin-bottom:6px'>Données insuffisantes (non estimées)</div>"
            f"<ul style='margin:0 0 0 18px;padding:0;color:#5c4d00;font-size:13px'>{items}</ul></div>"
        )

    fallback_note = ""
    if not result.llm_used:
        fallback_note = (
            "<div style='background:#fdecec;border:1px solid #f0b8b8;border-radius:6px;padding:12px 16px;margin:18px 0;"
            "color:#a12622;font-size:13px'>"
            "Analyse qualitative LLM indisponible — ce rapport repose uniquement sur les signaux quantitatifs.</div>"
        )

    watermark_line = ""
    if recipient:
        watermark_line = (
            f"<div style='margin-top:4px'>Édition personnelle de <strong>{_esc(recipient)}</strong> — {_esc(generated_at)}. "
            "Toute diffusion engage la responsabilité du destinataire.</div>"
        )

    gauge = _potentiel_gauge(result.potentiel)
    scenarios_html = _scenarios_block(result.scenarios)
    methodology_html = _methodology_block(result)
    body_html = _render_markdown_body(result.rapport_detaille)

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#eef1f6;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" style="border-collapse:collapse;background:#eef1f6"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="640" style="max-width:640px;width:100%;border-collapse:collapse;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(11,31,58,.12)">
  <tr><td style="background:linear-gradient(135deg,{_ACCENT},{_ACCENT_SOFT});padding:22px 28px;border-bottom:3px solid {_GOLD}">
    <table role="presentation" width="100%" style="border-collapse:collapse"><tr>
      <td style="width:60px;vertical-align:middle">{emblem_img}</td>
      <td style="vertical-align:middle;padding-left:14px">
        <div style="color:#ffffff;font-size:19px;font-weight:700;letter-spacing:.03em;font-family:Georgia,serif">{_esc(BRAND)}</div>
        <div style="color:{_GOLD_SOFT};font-size:12px;margin-top:2px;letter-spacing:.04em">NOTE DE RECHERCHE — ANALYSE D'INVESTISSEMENT</div>
      </td>
      <td style="vertical-align:middle;text-align:right;white-space:nowrap">
        <div style="display:inline-block;background:rgba(255,255,255,.10);border:1px solid {_GOLD_SOFT};border-radius:20px;padding:5px 12px;color:#ffffff;font-size:11px">Confiance : {_esc(result.confiance_globale)}</div>
      </td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:20px 28px 0">
    <div style="font-size:12px;color:{_MUTE}">Token analysé</div>
    <div style="font-size:15px;font-weight:600;color:{_INK};word-break:break-all">{_esc(result.contract)}</div>
    <div style="font-size:12px;color:#9aa4b1;margin-top:4px">Généré le {_esc(generated_at)}</div>
    {tldr_block}
  </td></tr>
  <tr><td style="padding:0 28px">
    {gauge}
    <table role="presentation" width="100%" style="border-collapse:collapse"><tr>
      {_badge("Risque", result.risque, risk_color)}
      {_badge("Recommandation", result.recommandation, reco_color)}
    </tr></table>
  </td></tr>
  <tr><td style="padding:6px 28px">
    {fallback_note}
    <div style="background:#f7f9fc;border-radius:8px;padding:14px 18px;margin:14px 0">
      <div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{_MUTE};margin-bottom:4px">Thèse</div>
      <div style="font-size:14px;line-height:1.6;color:{_INK}">{_esc(result.these)}</div>
    </div>
    {order_block}
    {scenarios_html}
    {gaps_block}
    <div style="margin-top:10px">{body_html}</div>
    {methodology_html}
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
