"""Rapport VC en PDF sécurisé — pièce jointe email (édition « impossible à copier »).

Le PDF est un rendu PARALLÈLE au rapport HTML (`vc_report.py`) : même données
(``VCResult``), mêmes libellés (`vc_i18n.report_strings`), même palette de marque —
mais produit avec ``reportlab`` (pur Python, aucune dépendance système) plutôt que
reconverti depuis le HTML, pour un rendu maîtrisé et fiable en CI/Docker.

## Sécurité — ce que « impossible à copier » signifie réellement

Le PDF est chiffré (`pypdf`) avec des permissions refusant l'extraction de texte
et de contenu (copier-coller) dans les lecteurs qui respectent la norme PDF. Ce
n'est PAS un chiffrement inviolable : un outil de suppression de permissions (il
en existe des gratuits) neutralise cette protection en quelques secondes. C'est
la norme du marché pour ce type de document — un frein sérieux à la copie
occasionnelle, jamais une garantie cryptographique. Le filigrane nominatif
(destinataire + empreinte SHA-256, déjà utilisé côté HTML) reste la VRAIE
protection en cas de fuite : il rend la fuite traçable.

Tout champ dynamique passe par ``_esc`` (identique au rendu HTML) avant
insertion dans un ``Paragraph`` — même défense en profondeur contre une donnée
LLM hostile.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from aria_core.skills.vc_analysis import VCResult
from aria_core.skills.vc_i18n import confidence_label, norm_lang, report_strings, risk_label
from aria_core.skills.vc_report import _esc, _fmt_compact_usd, _report_title, report_integrity

_NAVY = HexColor("#0b1220")
_NAVY_LIGHT = HexColor("#12325c")
_GOLD = HexColor("#c9a227")
_GOLD_LIGHT = HexColor("#e6c463")
_EMERALD = HexColor("#1f8a74")
_ROSE = HexColor("#d98a8a")
_RUST = HexColor("#a34a2a")
_IVORY = HexColor("#f6f2e9")
_INK = HexColor("#2a2620")
_MUTE = HexColor("#7a7264")

_RECO_BG = {
    "BUY": _GOLD, "WATCH": colors.white, "SELL": _RUST, "AVOID": colors.white,
}
_RECO_FG = {
    "BUY": _NAVY, "WATCH": _INK, "SELL": _IVORY, "AVOID": _MUTE,
}

_PAGE_W, _PAGE_H = LETTER
_MARGIN = 20 * mm


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=22, leading=26,
                                 textColor=_IVORY, alignment=TA_CENTER),
        "kicker": ParagraphStyle("kicker", fontName="Helvetica-Bold", fontSize=8, leading=11,
                                  textColor=_GOLD_LIGHT, alignment=TA_CENTER, spaceAfter=2),
        "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=8, leading=11,
                                textColor=colors.HexColor("#93a09b"), alignment=TA_CENTER),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=12, leading=15,
                                   textColor=_INK, spaceBefore=14, spaceAfter=6,
                                   borderColor=_GOLD, borderWidth=0, leftIndent=0),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=14, textColor=_INK),
        "quote": ParagraphStyle("quote", fontName="Helvetica-Oblique", fontSize=9.5, leading=14,
                                 textColor=_INK, leftIndent=10, borderColor=_GOLD),
        "note": ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=7.5, leading=11,
                                textColor=_MUTE),
        "small_label": ParagraphStyle("small_label", fontName="Helvetica-Bold", fontSize=7.5,
                                       leading=10, textColor=_MUTE),
        "value": ParagraphStyle("value", fontName="Helvetica-Bold", fontSize=10.5, leading=13,
                                 textColor=_INK, alignment=TA_LEFT),
        "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=7.5, leading=11,
                                  textColor=colors.HexColor("#93a09b"), alignment=TA_CENTER),
    }


def _section_title(text: str, st: dict) -> Table:
    """Titre de section : trait or + libellé (équivalent PDF de `_section_header` HTML)."""
    bar = Table([[""]], colWidths=[6 * mm], rowHeights=[2])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _GOLD)]))
    row = Table(
        [[bar, Paragraph(_esc(text), ParagraphStyle("sec", parent=st["section"], spaceBefore=0, spaceAfter=0))]],
        colWidths=[8 * mm, None],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether([Spacer(1, 10), row, Spacer(1, 4)])


def _badges_table(result: VCResult, s: dict, lang: str, st: dict) -> Table:
    potentiel = s["potential_label"].format(n=result.potentiel) if result.potentiel is not None else s["potential_na"]
    cells = [
        result.recommandation,
        s["confidence_prefix"].format(v=confidence_label(result.confiance_globale, lang)),
        potentiel,
        s["risk_prefix"].format(v=risk_label(result.risque, lang)),
    ]
    reco_bg = _RECO_BG.get(result.recommandation, colors.white)
    reco_fg = _RECO_FG.get(result.recommandation, _MUTE)
    style = ParagraphStyle("badge", fontName="Helvetica-Bold", fontSize=7.5, alignment=TA_CENTER, textColor=_INK)
    paras = [Paragraph(_esc(c), style) for c in cells]
    t = Table([paras], colWidths=[None] * 4)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 0.5, _GOLD),
        ("BOX", (1, 0), (1, 0), 0.5, _GOLD),
        ("BOX", (2, 0), (2, 0), 0.5, _GOLD),
        ("BOX", (3, 0), (3, 0), 0.5, _GOLD),
        ("BACKGROUND", (0, 0), (0, 0), reco_bg),
        ("TEXTCOLOR", (0, 0), (0, 0), reco_fg),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _rr_flowables(result: VCResult, s: dict, st: dict) -> list:
    rr = result.rr
    if rr is None:
        return []
    upside = result.upside_pct or 0.0
    downside = result.downside_pct or 0.0
    if rr >= 3:
        qualifier = s["rr_qualifier_strong"]
    elif rr >= 1.5:
        qualifier = s["rr_qualifier_good"]
    elif rr >= 1:
        qualifier = s["rr_qualifier_balanced"]
    else:
        qualifier = s["rr_qualifier_weak"]
    caption = s["rr_caption"].format(qualifier=qualifier, upside=upside, downside=downside)
    if downside and downside < 4 and rr >= 4:
        caption += s["rr_tight_stop"].format(downside=downside)

    label_style = ParagraphStyle("rrlabel", fontName="Helvetica-Bold", fontSize=7, alignment=TA_CENTER,
                                  textColor=_GOLD_LIGHT)
    big_style = ParagraphStyle("rrbig", fontName="Helvetica-Bold", fontSize=18, alignment=TA_CENTER,
                                textColor=_GOLD_LIGHT)
    up_style = ParagraphStyle("rrup", fontName="Helvetica-Bold", fontSize=13, alignment=TA_CENTER,
                               textColor=colors.HexColor("#35b295"))
    down_style = ParagraphStyle("rrdown", fontName="Helvetica-Bold", fontSize=13, alignment=TA_CENTER,
                                 textColor=_ROSE)
    header_row = [
        Paragraph(_esc(s["rr_upside_label"]), label_style),
        Paragraph(_esc(s["rr_ratio_label"]), label_style),
        Paragraph(_esc(s["rr_downside_label"]), label_style),
    ]
    value_row = [
        Paragraph(f"+{upside:.0f}%", up_style),
        Paragraph(f"{rr:.1f}", big_style),
        Paragraph(f"−{downside:.0f}%", down_style),
    ]
    t = Table([header_row, value_row], colWidths=[None] * 3)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, _GOLD),
        ("BACKGROUND", (0, 0), (-1, -1), _NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
    ]))
    cap_style = ParagraphStyle("rrcap", fontName="Helvetica", fontSize=7.5, alignment=TA_CENTER,
                                textColor=colors.HexColor("#93a09b"), backColor=_NAVY)
    cap = Table([[Paragraph(_esc(caption), cap_style)]], colWidths=[None])
    cap.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, _GOLD), ("LINEABOVE", (0, 0), (-1, 0), 0, _NAVY),
        ("BACKGROUND", (0, 0), (-1, -1), _NAVY), ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [Spacer(1, 8), t, cap, Spacer(1, 4)]


def _kv_table(rows: list[tuple[str, str]], st: dict, *, highlight_last: bool = False) -> Table:
    label_style = ParagraphStyle("kvlabel", fontName="Helvetica-Bold", fontSize=7.5, textColor=_MUTE)
    value_style = ParagraphStyle("kvvalue", fontName="Helvetica-Bold", fontSize=10, textColor=_INK,
                                  alignment=2)
    gold_value_style = ParagraphStyle("kvvaluegold", parent=value_style, textColor=HexColor("#8a6d1f"))
    data = []
    for i, (label, value) in enumerate(rows):
        vs = gold_value_style if (highlight_last and i == len(rows) - 1) else value_style
        data.append([Paragraph(_esc(label), label_style), Paragraph(value, vs)])
    t = Table(data, colWidths=[None, None])
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(len(rows) - 1):
        style.append(("LINEBELOW", (0, i), (-1, i), 0.5, HexColor("#e7deca")))
    t.setStyle(TableStyle(style))
    return t


def _order_flowables(result: VCResult, capital_usd: float | None, s: dict, st: dict) -> list:
    if not result.actionable:
        return []
    rows: list[tuple[str, str]] = [(s["order_reco"], _esc(result.recommandation))]
    if result.recommandation == "BUY":
        rows.append((s["order_size"], _esc(s["order_size_value"].format(pct=result.taille_pct))))
        if capital_usd and capital_usd > 0:
            position = capital_usd * result.taille_pct / 100
            rows.append((s["order_capital_to_position"], f"${capital_usd:,.0f} &rarr; ${position:,.0f}"))
    rows.append((s["order_entry"], _esc(result.entree)))
    rows.append((s["order_invalidation"], _esc(result.invalidation)))
    rows.append((s["order_target"], _esc(result.cible)))
    return [
        _section_title(s["order_section"], st),
        _kv_table(rows, st, highlight_last=True),
        Paragraph(_esc(s["order_disclaimer"]), st["note"]),
        Spacer(1, 4),
    ]


def _dollar_flowables(result: VCResult, capital_usd: float | None, s: dict, st: dict) -> list:
    if not (
        capital_usd and capital_usd > 0 and result.recommandation == "BUY" and result.taille_pct > 0
        and result.upside_pct is not None and result.downside_pct is not None
    ):
        return []
    position = capital_usd * result.taille_pct / 100
    gain = position * result.upside_pct / 100
    loss = position * result.downside_pct / 100
    target_value = position + gain
    position_line = s["dollar_position_line"].format(position=f"${position:,.0f}", target=f"${target_value:,.0f}")
    risk_line = s["dollar_risk_line"].format(inval=_esc(result.invalidation))
    rows = [
        (position_line, f"+${gain:,.0f}"),
        (risk_line, f"−${loss:,.0f}"),
    ]
    return [
        _section_title(s["dollar_section"], st),
        _kv_table(rows, st),
        Paragraph(_esc(s["dollar_scale_note"]), st["note"]),
        Spacer(1, 4),
    ]


def _thesis_flowables(result: VCResult, s: dict, st: dict) -> list:
    out = []
    if result.resume_executif:
        out += [
            _section_title(s["tldr_section"], st),
            Paragraph(f"«&nbsp;{_esc(result.resume_executif)}&nbsp;»", st["quote"]),
        ]
    if result.these:
        out += [
            _section_title(s["these_section"], st),
            Paragraph(_esc(result.these), st["body"]),
        ]
    return out


_SCEN_TITLE_KEY = {"bull": "scen_bull", "base": "scen_base", "bear": "scen_bear"}


def _scenarios_flowables(scenarios: list[dict], s: dict, lang: str, st: dict) -> list:
    if not scenarios:
        return []
    title_style = ParagraphStyle("sctitle", fontName="Helvetica-Bold", fontSize=7.5, textColor=_MUTE,
                                  alignment=TA_CENTER)
    value_style = ParagraphStyle("scvalue", fontName="Helvetica-Bold", fontSize=13, textColor=_INK,
                                  alignment=TA_CENTER)
    sub_style = ParagraphStyle("scsub", fontName="Helvetica-Oblique", fontSize=7, textColor=_MUTE,
                                alignment=TA_CENTER)
    cells = []
    for sc in scenarios:
        nom = str(sc.get("nom", ""))
        title = s.get(_SCEN_TITLE_KEY.get(nom, ""), nom)
        proba = max(0, min(100, int(sc.get("probabilite") or 0)))
        conf = confidence_label(sc.get("confiance", "faible"), lang)
        cell = [
            Paragraph(_esc(title), title_style),
            Paragraph(_esc(str(sc.get("cible", ""))), value_style),
            Paragraph(f"{s['scen_probability_label']}&nbsp;{proba}%", sub_style),
            Paragraph(_esc(s["scen_confidence_label"].format(v=conf)), sub_style),
        ]
        cells.append(cell)
    t = Table([cells], colWidths=[None] * len(cells))
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e0d6ba")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#e0d6ba")),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return [_section_title(s["scenarios_section"], st), t, Spacer(1, 4)]


def _ta_flowables(result: VCResult, s: dict, st: dict) -> list:
    if not result.ta_levels_lines and not result.chart_data_uri:
        return []
    out = [_section_title(s["ta_section"], st)]
    trend = f" &middot; {s['ta_trend_prefix']} {_esc(result.ta_trend)}" if result.ta_trend else ""
    out.append(Paragraph(f"{_esc(s['ta_ohlcv_real'])}{trend}", st["small_label"]))
    for line in result.ta_levels_lines:
        out.append(Paragraph(f"&#8226; {_esc(line)}", st["body"]))
    if result.chart_data_uri.startswith("data:image/png"):
        try:
            import base64

            b64 = result.chart_data_uri.split(",", 1)[1]
            raw = base64.b64decode(b64)
            img_reader = ImageReader(io.BytesIO(raw))
            iw, ih = img_reader.getSize()
            max_w = _PAGE_W - 2 * _MARGIN
            ratio = max_w / iw
            out.append(Spacer(1, 6))
            out.append(Image(io.BytesIO(raw), width=max_w, height=ih * ratio))
        except Exception:  # noqa: BLE001 — un graphique corrompu n'empêche jamais le PDF
            pass
    return out + [Spacer(1, 4)]


def _roi_flowables(result: VCResult, s: dict, st: dict) -> list:
    if not result.roi_scenarios:
        return []
    basis_label = s["roi_basis_fdv"] if result.roi_basis == "fdv" else s["roi_basis_mcap"]
    sector = _esc(result.roi_sector) if result.roi_sector else ""
    sector_line = (
        s["roi_sector_line"].format(sector=sector)
        if (result.roi_sector_recognized and sector)
        else s["roi_sector_unknown"]
    )
    label_style = ParagraphStyle("roilabel", fontName="Helvetica", fontSize=9, textColor=_INK)
    sub_style = ParagraphStyle("roisub", fontName="Helvetica", fontSize=7.5, textColor=_MUTE)
    mult_style = ParagraphStyle("roimult", fontName="Helvetica-Bold", fontSize=12, textColor=HexColor("#b0862b"),
                                 alignment=2)
    rows = []
    for sc in result.roi_scenarios:
        label = _esc(sc.get("label", ""))
        ref = _fmt_compact_usd(sc.get("ref_mcap_usd", 0))
        mult = sc.get("multiple", 0)
        ref_line = _esc(s["roi_ref_label"].format(basis=basis_label, ref=ref))
        note = sc.get("note", "")
        cell = [Paragraph(label, label_style), Paragraph(ref_line, sub_style)]
        if note:
            cell.append(Paragraph(_esc(note), sub_style))
        rows.append([cell, Paragraph(f"{mult:g}x", mult_style)])
    t = Table(rows, colWidths=[None, 25 * mm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, HexColor("#e7deca")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    disclaimer = _esc(result.roi_disclaimer or s["roi_disclaimer_default"])
    return [
        _section_title(s["roi_section"], st),
        Paragraph(_esc(sector_line), st["small_label"]),
        Spacer(1, 4), t,
        Paragraph(disclaimer, st["note"]),
        Spacer(1, 4),
    ]


def _detailed_flowables(result: VCResult, tier: str, s: dict, st: dict) -> list:
    if tier == "standard":
        return [
            _section_title(s["detailed_section"], st),
            Paragraph(_esc(s["standard_teaser"]), st["note"]),
        ]
    out = [_section_title(s["detailed_section"], st)]
    for para in (result.rapport_detaille or "").split("\n"):
        stripped = para.strip()
        if not stripped:
            continue
        safe = _esc(stripped)
        import re as _re

        safe = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
        if stripped.startswith(("# ", "## ", "### ")):
            out.append(Paragraph(safe.lstrip("#").strip(), st["small_label"]))
        elif stripped[:2] in ("- ", "* "):
            out.append(Paragraph(f"&#8226; {safe[2:]}", st["body"]))
        else:
            out.append(Paragraph(safe, st["body"]))
    return out + [Spacer(1, 4)]


def _methodology_flowables(s: dict, st: dict) -> list:
    sources = s["methodology_sources"]
    label_style = ParagraphStyle("methlabel", fontName="Helvetica-Bold", fontSize=7.5, textColor=_MUTE)
    value_style = ParagraphStyle("methvalue", fontName="Helvetica", fontSize=9.5, textColor=_INK, alignment=2)
    rows = [[Paragraph(_esc(name), label_style), Paragraph(_esc(desc), value_style)] for name, desc in sources]
    t = Table(rows, colWidths=[None, None])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, HexColor("#ece3cd")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        _section_title(s["methodology_section"], st), t,
        Paragraph(_esc(s["methodology_principle"]), st["note"]),
        Spacer(1, 4),
    ]


def _gaps_flowables(gaps: list[str], s: dict, st: dict) -> list:
    if not gaps:
        return []
    out = [Paragraph(_esc(s["gaps_title"]), st["small_label"])]
    for g in gaps:
        out.append(Paragraph(f"&#9671; {_esc(g)}", st["body"]))
    out.append(Paragraph(_esc(s["gaps_footer"]), st["note"]))
    return [Spacer(1, 6)] + out + [Spacer(1, 4)]


def _references_flowables(links: list[dict], s: dict, st: dict) -> list:
    safe = [
        link for link in (links or [])
        if str(link.get("url", "")).strip().lower().startswith(("http://", "https://"))
    ]
    out = [_section_title(s["refs_title"], st)]
    if not safe:
        out.append(Paragraph(_esc(s["refs_none"]), st["note"]))
    else:
        link_style = ParagraphStyle("reflink", fontName="Helvetica", fontSize=8.5, textColor=_NAVY_LIGHT)
        for link in safe:
            url = _esc(link["url"])
            label = _esc(link["label"])
            out.append(Paragraph(f'<link href="{url}">{label}</link>', link_style))
        out.append(Paragraph(_esc(s["refs_disclaimer"]), st["note"]))
    return out + [Spacer(1, 4)]


def render_pdf_report(
    result: VCResult,
    *,
    generated_at: str,
    recipient: str | None = None,
    report_number: int | None = None,
    series_number: int | None = None,
    capital_usd: float | None = None,
    tier: str = "premium",
    lang: str = "fr",
) -> bytes:
    """Rend le rapport VC complet en PDF (bytes, non chiffré). Voir `secure_pdf_bytes`
    pour la protection anti-copie appliquée avant envoi. Miroir de `render_html_report` :
    mêmes données, mêmes libellés (`report_strings`), data-gated à l'identique."""
    lang = norm_lang(lang)
    s = report_strings(lang)
    st = _styles()
    is_standard = tier == "standard"
    ref_id, full_hash = report_integrity(result, generated_at=generated_at, recipient=recipient)
    title = _report_title(result)
    tier_label = s["tier_standard_label"] if is_standard else s["tier_premium_label"]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN, bottomMargin=_MARGIN,
        title=s["html_title"].format(title=title),
    )

    story: list = []

    # ── Bandeau hero (navy) ──────────────────────────────────────────────
    hero_lines = [
        Paragraph(_esc(s["confidential"]).upper() + " &middot; " + _esc(tier_label), st["kicker"]),
        Spacer(1, 4),
        Paragraph("ARIA", ParagraphStyle("wordmark", fontName="Helvetica-Bold", fontSize=30,
                                          textColor=_GOLD_LIGHT, alignment=TA_CENTER)),
        Paragraph(_esc(s["vanguard_zhc_kicker"]), st["kicker"]),
        Spacer(1, 8),
        Paragraph(_esc(s["research_note_kicker"]), st["kicker"]),
        Paragraph(_esc(title), st["title"]),
        Paragraph(_esc(s["network_label"]), st["kicker"]),
        Paragraph(_esc(result.contract), ParagraphStyle("addr", fontName="Courier", fontSize=8,
                                                          textColor=colors.HexColor("#93a09b"),
                                                          alignment=TA_CENTER)),
    ]
    meta_parts = []
    if series_number:
        meta_parts.append(s["meta_series"].format(n=_esc(str(series_number))))
    if report_number:
        meta_parts.append(s["meta_report_num"].format(n=_esc(str(report_number))))
    meta_parts.append(s["meta_generated"].format(date=_esc(generated_at)))
    hero_lines.append(Paragraph(" &middot; ".join(meta_parts), st["meta"]))
    hero_lines.append(Spacer(1, 10))
    hero_lines.append(_badges_table(result, s, lang, st))

    hero_table = Table([[hero_lines]], colWidths=[_PAGE_W - 2 * _MARGIN])
    hero_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _NAVY),
        ("BOX", (0, 0), (-1, -1), 1, _GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 18), ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(hero_table)
    story += _rr_flowables(result, s, st)
    story.append(Spacer(1, 14))

    # ── Corps ivoire ──────────────────────────────────────────────────────
    if not result.llm_used:
        story.append(Paragraph(f"<b>{_esc(s['fallback_title'])}</b>. {_esc(s['fallback_body'])}", st["note"]))
        story.append(Spacer(1, 6))

    story += _thesis_flowables(result, s, st)
    story += _order_flowables(result, capital_usd, s, st)
    story += _dollar_flowables(result, capital_usd, s, st)
    story += _scenarios_flowables(result.scenarios, s, lang, st)
    if not is_standard:
        story += _ta_flowables(result, s, st)
        story += _roi_flowables(result, s, st)
    story += _gaps_flowables(result.donnees_insuffisantes, s, st)
    story += _detailed_flowables(result, tier, s, st)
    if not is_standard:
        story += _methodology_flowables(s, st)
        story += _references_flowables(result.liens_projet, s, st)

    # ── Filigrane d'édition personnelle (traçabilité anti-fuite) ─────────
    if recipient:
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            _esc(s["watermark_personal"].format(recipient=recipient, ref=ref_id)),
            ParagraphStyle("wm", fontName="Helvetica-Bold", fontSize=8, textColor=HexColor("#c9a962"),
                           alignment=TA_CENTER),
        ))

    # ── Pied légal ────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", color=_GOLD, thickness=0.5))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"{_esc(s['footer_ref_label'])} {_esc(ref_id)} &middot; "
        f"{_esc(s['footer_dated'].format(date=generated_at, hash=full_hash))}",
        st["footer"],
    ))
    bold = f"<b>{_esc(s['footer_disclaimer_bold'])}</b>"
    story.append(Paragraph(_esc(s["footer_disclaimer"]).format(bold=bold), st["footer"]))
    story.append(Paragraph(_esc(s["copyright"]), st["footer"]))

    doc.build(story)
    return buf.getvalue()


# ── Sécurisation anti-copie (permissions PDF, jamais un chiffrement inviolable) ──

def secure_pdf_bytes(raw_pdf: bytes, *, owner_password: str) -> bytes:
    """Chiffre le PDF : mot de passe UTILISATEUR vide (s'ouvre sans friction),
    mot de passe PROPRIÉTAIRE requis pour lever les permissions. Permissions
    refusées : extraction de texte/graphiques (copier-coller), modification,
    assemblage, impression haute-fidélité. Impression basique autorisée.

    ``owner_password`` : secret jetable généré par l'appelant, jamais réutilisé
    ni stocké (l'objectif est de restreindre les permissions, pas de protéger
    l'ouverture — cf. avertissement en tête de module : dissuasif, pas inviolable).
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import UserAccessPermissions as Perm

    reader = PdfReader(io.BytesIO(raw_pdf))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    permissions = Perm.PRINT  # impression basique seule ; pas d'extraction/modification/assemblage
    writer.encrypt(user_password="", owner_password=owner_password, permissions_flag=permissions)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

