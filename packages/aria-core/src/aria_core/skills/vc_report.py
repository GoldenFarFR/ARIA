"""Rendu du rapport VC en HTML « qualité institutionnelle » (design B4).

Transforme un ``VCResult`` en document HTML autonome (CSS inline, emblème embarqué
en base64 — aucun asset externe requis à l'affichage) destiné à l'email ARIA — et,
plus tard, au site / à l'abonnement. Le design (maquette « B4 ») est un hero sombre
nocturne or/émeraude (goutte dorée + wordmark ARIA) suivi d'un corps ivoire et d'un
pied « certificat » — chaque élément visuel de la maquette est ici piloté par les
champs réels de ``VCResult`` plutôt que codé en dur.

## Sécurité (extension du dôme à la couche rendu)

Le contenu du rapport est produit par le LLM, qui a lui-même traité des données
non fiables (nom du token, catégories, etc.). **Tout champ dynamique est
HTML-échappé** (`html.escape`, via `_esc`) avant d'être injecté dans le template —
sans exception, y compris les champs déjà validés par des allowlists en amont
(défense en profondeur). Le mini-rendu markdown (`_render_markdown_body`) n'émet
que des balises de mise en forme contrôlées (`h2/h3/h4`, `p`, `ul/li`, `strong`) —
jamais de HTML fourni par le modèle. Les couleurs, styles de pastille et libellés
de structure proviennent exclusivement de dictionnaires allowlist indexés par une
valeur déjà validée en amont (jamais une chaîne construite à partir du LLM). Les
liens projet sont revalidés (schéma http/https) par `_references_block`. Cela
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
_ACCENT = "#0b1f3a"       # navy institutionnel — utilisé par _render_markdown_body (conservé tel quel)
_ACCENT_SOFT = "#12325c"
_GOLD = "#c9a227"         # or — accent luxe (identique au thème B4)
_GOLD_SOFT = "#bfa15a"
_INK = "#1c2530"
_MUTE = "#6b7684"

_EMBLEM_PATH = Path(__file__).resolve().parents[1] / "assets" / "aria_emblem.png"

# ─────────────────────────── Palette B4 (hero nocturne / corps ivoire) ───────────────────────────
_GOLD_LIGHT = "#e6c463"
_GOLD_DEEP = "#b0862b"
_EMERALD = "#1f8a74"
_EMERALD_DEEP = "#0f6b5c"
_ROSE = "#d98a8a"
_RUST = "#a34a2a"
_IVORY = "#f6f2e9"
_INK_WARM = "#2a2620"
_MUTE_WARM = "#7a7264"
_NIGHT = "#070b14"

# Pastilles du hero : styles fixes indexés par une valeur déjà validée en amont
# (allowlist — vc_analysis.py clampe recommandation/risque/confiance_globale à un
# ensemble fermé). Jamais une couleur ou un libellé construit depuis le LLM.
_RECO_COLORS = {
    "BUY": "background-color:#c9a227;background-image:linear-gradient(135deg,#b0862b,#e6c463 55%,#c9a227);color:#10131f;",
    "WATCH": "border:1px solid rgba(201,162,39,0.55);background-color:rgba(201,162,39,0.08);color:#e6c463;",
    "SELL": "background-color:#a34a2a;background-image:linear-gradient(135deg,#7a3419,#c9603c 55%,#a34a2a);color:#f6f2e9;",
    "AVOID": "border:1px solid rgba(147,160,155,0.5);background-color:rgba(147,160,155,0.10);color:#93a09b;",
}
_RISK_COLORS = {
    "FAIBLE": "border:1px solid rgba(31,138,116,0.6);background-color:rgba(15,107,92,0.14);color:#e6c463;",
    "MODÉRÉ": "border:1px solid rgba(31,138,116,0.6);background-color:rgba(15,107,92,0.14);color:#e6c463;",
    "ÉLEVÉ": "border:1px solid rgba(201,162,39,0.55);background-color:rgba(201,162,39,0.08);color:#e6c463;",
    "EXTRÊME": "border:1px solid rgba(217,138,138,0.6);background-color:rgba(163,74,42,0.18);color:#d98a8a;",
}
_CONF_COLORS = {
    "haute": "border:1px solid rgba(31,138,116,0.6);background-color:rgba(15,107,92,0.14);color:#e6c463;",
    "moyenne": "border:1px solid rgba(201,162,39,0.55);background-color:rgba(201,162,39,0.08);color:#e6c463;",
    "faible": "border:1px solid rgba(217,138,138,0.6);background-color:rgba(163,74,42,0.18);color:#d98a8a;",
}
_DEFAULT_PILL = "border:1px solid rgba(147,160,155,0.5);background-color:rgba(147,160,155,0.10);color:#93a09b;"
_POTENTIEL_PILL = "border:1px solid rgba(201,162,39,0.55);background-color:rgba(201,162,39,0.08);color:#e6c463;"

# Scénarios : titre affiché + style de barre de probabilité, indexés par ``nom``
# (allowlist fermée dans vc_analysis._SCENARIO_NAMES : bull/base/bear uniquement).
_SCEN_META = {
    "bull": ("Haussier", "background-color:#1f8a74;background-image:linear-gradient(90deg,#0f6b5c,#1f8a74);", False),
    "base": ("Central · Référence", "background-color:#c9a227;background-image:linear-gradient(90deg,#b0862b,#e6c463);", True),
    "bear": ("Baissier", "background-color:#a34a2a;", False),
}

# Sources méthodologiques — contenu statique de l'annexe « Méthodologie & sources »
# (jamais dérivé du LLM ; documente d'où viennent les données factuelles utilisées).
_METHODOLOGY_SOURCES = (
    ("DexScreener", "marché & liquidité"),
    ("Blockscout Base", "on-chain, holders, audit"),
    ("CoinGecko", "market cap, FDV, supply"),
    ("Smart-money", "heuristique propriétaire"),
    ("Rédaction", "Claude Opus via Spark · contrôle anti-hallucination"),
)

# ─────────────────────────── Tiers (éditions premium / standard) ───────────────────────────
# Deux éditions du même rapport : le design (or, émeraude, corps ivoire, structure)
# est identique — seules les surfaces sombres (hero, pied « certificat », bandes
# guilloché) et le bandeau de tier changent de teinte. Allowlist fermée, jamais
# dérivée d'une valeur fournie par le LLM : `tier` ne fait que sélectionner l'une
# des deux entrées ci-dessous et masquer des sections, jamais injecter du texte.
_TIER_PREMIUM = "premium"
_TIER_STANDARD = "standard"

_TIER_THEMES = {
    _TIER_PREMIUM: {
        "label": "RAPPORT PREMIUM",
        # Pastille dorée — même style que la pastille de recommandation BUY.
        "pill_style": _RECO_COLORS["BUY"],
        # Un seul linear-gradient (pas de radial-gradient) : sur mobile (Gmail
        # WebView notamment), un radial-gradient sur une grande surface force
        # une re-rastérisation à chaque frame de scroll ("effet saccadé,
        # dessiné ligne par ligne"). Un linear-gradient à stops fixes est
        # composité une fois et ne coûte rien au scroll.
        "hero_bg": (
            "background-color:#0a0e1a;"
            "background-image:linear-gradient(165deg, #101a2e 0%, #0a0e1a 55%, #0c1020 100%);"
        ),
        "dark_base": "#0b1220",   # bandes guilloché (haut/bas) + fond de secours du pied
        "deep_base": "#080c16",   # bande microprint + point le plus sombre du dégradé du pied
        "footer_mid": "#0d1424",  # point clair du dégradé du pied « certificat »
    },
    _TIER_STANDARD: {
        "label": "RAPPORT STANDARD",
        # Pastille rose discrète — bordure + fond translucide (pas de fond plein).
        "pill_style": "border:1px solid rgba(217,138,138,0.55);background-color:rgba(217,138,138,0.10);color:#e8b9b9;",
        "hero_bg": (
            "background-color:#1c0e18;"
            "background-image:linear-gradient(165deg, #2a1622 0%, #1c0e18 55%, #22111c 100%);"
        ),
        "dark_base": "#1c0e18",
        "deep_base": "#160b12",
        "footer_mid": "#22111c",
    },
}


def _theme(tier: str) -> dict:
    """Résout la palette et le libellé du tier — allowlist fermée (défense en profondeur).

    Toute valeur autre que ``"standard"`` retombe sur ``"premium"`` (défaut sûr) :
    un tier absent, mal orthographié ou falsifié en amont ne doit jamais faire
    basculer silencieusement le rendu vers un état non prévu. Les couleurs et
    libellés proviennent exclusivement de ``_TIER_THEMES`` — jamais dérivés
    d'une chaîne construite à partir du LLM.
    """
    resolved = _TIER_STANDARD if tier == _TIER_STANDARD else _TIER_PREMIUM
    theme = dict(_TIER_THEMES[resolved])
    theme["tier"] = resolved
    return theme

_FONT_SANS = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_FONT_NUM = "'Helvetica Neue',-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
_FONT_SERIF = "Georgia,'Times New Roman',serif"
_FONT_MONO = "ui-monospace,Menlo,Consolas,'Courier New',monospace"


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


def _references_block(links: list[dict]) -> str:
    """Liens officiels déclarés par le projet (site, X, Telegram…) — vérifiez par vous-même.

    Revalidation stricte du schéma http(s) à ce dernier point d'entrée avant
    tout `<a href>` cliquable — défense en profondeur : ces liens viennent
    d'un tiers non fiable (DexScreener relaie ce que le projet déclare, ARIA
    ne le vérifie pas). Toute URL hors http(s) est silencieusement écartée.
    """
    safe = [
        link for link in (links or [])
        if str(link.get("url", "")).strip().lower().startswith(("http://", "https://"))
    ]
    if not safe:
        body = f"<div style='font-size:12px;color:{_MUTE}'>Aucun lien officiel disponible pour ce token.</div>"
    else:
        items = "".join(
            f"<a href='{_esc(link['url'])}' style='display:inline-block;margin:0 10px 6px 0;padding:5px 12px;"
            f"background:#eef1f6;border-radius:14px;font-size:12px;color:{_ACCENT};text-decoration:none'>"
            f"{_esc(link['label'])}</a>"
            for link in safe
        )
        body = f"<div>{items}</div>"
    return (
        "<div style='margin:16px 0 4px;background:#f7f9fc;border-radius:8px;padding:14px 18px'>"
        f"<div style='font-family:Georgia,serif;font-size:15px;color:{_ACCENT};margin-bottom:8px'>Références : vérifiez par vous-même</div>"
        f"{body}"
        f"<div style='margin-top:8px;font-size:11px;color:{_MUTE}'>Liens déclarés par le projet (source : DexScreener). Non vérifiés par ARIA.</div>"
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


def email_subject(
    result: VCResult, *, generated_at: str | None = None, report_number: int | None = None
) -> str:
    """Objet d'email concis et professionnel.

    Inclut la date et le n° de rapport (si fournis) : indispensable pour trier
    ses rapports une fois abonné et destinataire de plusieurs analyses suivies
    du même token dans la durée.
    """
    potentiel = f"{result.potentiel}/10" if result.potentiel is not None else "n/a"
    date_part = f"{generated_at.split(' ')[0]} · " if generated_at else ""
    num_part = f"n°{report_number} · " if report_number else ""
    return (
        f"[{BRAND}] {num_part}{date_part}Analyse VC · {result.recommandation} · "
        f"Potentiel {potentiel} · {result.contract[:10]}…"
    )


def _format_serial(n: int) -> str:
    """Numéro de série façon édition numérotée : 5 chiffres, ex. 47 -> '00.047'."""
    padded = f"{max(0, int(n)):05d}"
    return f"{padded[:2]}.{padded[2:]}"


# ═══════════════════════════ Helpers visuels privés (design B4) ═══════════════════════════


def _report_title(result: VCResult) -> str:
    """Titre du hero : symbole du token si connu, sinon adresse tronquée (jamais inventé)."""
    symbol = (result.symbol or "").strip()
    if symbol:
        return symbol
    return f"{result.contract[:10]}…" if result.contract else "Token"


def _pill(label: str, style: str) -> str:
    return (
        "<span class=\"kpi\" style=\"display:inline-block;margin:4px 3px;padding:9px 15px;"
        f"border-radius:999px;{style}font-family:{_FONT_SANS};"
        "font-size:11px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;line-height:1;\">"
        f"{_esc(label)}</span>"
    )


def _badges_html(result: VCResult) -> str:
    potentiel_label = f"Potentiel {result.potentiel}/10" if result.potentiel is not None else "Potentiel n/a"
    pills = [
        _pill(result.recommandation, _RECO_COLORS.get(result.recommandation, _DEFAULT_PILL)),
        _pill(f"Confiance {result.confiance_globale}", _CONF_COLORS.get(result.confiance_globale, _DEFAULT_PILL)),
        _pill(potentiel_label, _POTENTIEL_PILL),
        _pill(f"Risque {result.risque}", _RISK_COLORS.get(result.risque, _DEFAULT_PILL)),
    ]
    return "".join(pills)


def _section_header(title: str) -> str:
    """En-tête de section « corps ivoire » : trait or + titre serif + filet dégradé (design B4)."""
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td width="26" style="vertical-align:middle;"><div style="height:2px;background-color:{_GOLD};font-size:0;line-height:2px;">&nbsp;</div></td>
          <td style="width:12px;font-size:0;">&nbsp;</td>
          <td style="white-space:nowrap;font-family:{_FONT_SERIF};font-size:17px;line-height:1.3;font-weight:400;color:{_INK_WARM};vertical-align:middle;">{_esc(title)}</td>
          <td style="width:12px;font-size:0;">&nbsp;</td>
          <td style="vertical-align:middle;" width="100%"><div style="height:1px;background-image:linear-gradient(to right,#dcd2b4,rgba(15,107,92,0.35),rgba(220,210,180,0));font-size:0;line-height:1px;">&nbsp;</div></td>
        </tr>
      </table>"""


def _rr_block_html(result: VCResult) -> str:
    """Encadré R/R focal — jamais affiché si le ratio n'est pas estimable (pas de valeur inventée)."""
    rr = result.rr
    if rr is None:
        return ""
    upside = result.upside_pct or 0.0
    downside = result.downside_pct or 0.0
    if rr >= 3:
        qualifier = "Asymétrie très favorable"
    elif rr >= 1.5:
        qualifier = "Asymétrie favorable"
    elif rr >= 1:
        qualifier = "Asymétrie équilibrée"
    else:
        qualifier = "Asymétrie défavorable"
    caption = f"{qualifier} : la récompense visée représente {rr:.1f}× le risque consenti."
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:30px;">
        <tr>
          <td style="border-radius:12px;padding:2px;background-color:{_GOLD};background-image:linear-gradient(120deg,#8a6a13 0%,{_GOLD_LIGHT} 30%,{_GOLD} 55%,{_EMERALD} 85%,{_EMERALD_DEEP} 100%);">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0b1220;border-radius:10px;">
              <tr>
                <td style="padding:5px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid rgba(230,196,99,0.3);border-radius:7px;">
                    <tr>
                      <td style="padding:22px 18px 4px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                          <tr>
                            <td width="33%" align="center" style="vertical-align:bottom;padding:0 8px;font-family:{_FONT_NUM};font-size:11px;line-height:1.4;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD};">Potentiel haussier</td>
                            <td width="34%" align="center" style="vertical-align:bottom;padding:0 8px;border-left:1px solid rgba(230,196,99,0.18);border-right:1px solid rgba(230,196,99,0.18);font-family:{_FONT_NUM};font-size:11px;line-height:1.4;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD};">Ratio récompense-risque</td>
                            <td width="33%" align="center" style="vertical-align:bottom;padding:0 8px;font-family:{_FONT_NUM};font-size:11px;line-height:1.4;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD};">Risque baissier</td>
                          </tr>
                          <tr>
                            <td width="33%" align="center" style="vertical-align:bottom;padding:12px 8px 16px;">
                              <div style="font-family:{_FONT_NUM};font-size:22px;line-height:1;font-weight:600;letter-spacing:-0.01em;font-variant-numeric:lining-nums tabular-nums;color:#35b295;">+{_esc(f"{upside:.0f}")}%</div>
                            </td>
                            <td width="34%" align="center" style="vertical-align:bottom;padding:12px 8px 16px;border-left:1px solid rgba(230,196,99,0.18);border-right:1px solid rgba(230,196,99,0.18);">
                              <div style="font-family:{_FONT_NUM};font-size:40px;line-height:1;font-weight:700;letter-spacing:-0.01em;font-variant-numeric:lining-nums tabular-nums;color:{_GOLD_LIGHT};">{_esc(f"{rr:.1f}")}</div>
                            </td>
                            <td width="33%" align="center" style="vertical-align:bottom;padding:12px 8px 16px;">
                              <div style="font-family:{_FONT_NUM};font-size:22px;line-height:1;font-weight:600;letter-spacing:-0.01em;font-variant-numeric:lining-nums tabular-nums;color:{_ROSE};">&minus;{_esc(f"{downside:.0f}")}%</div>
                            </td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                    <tr>
                      <td align="center" style="padding:0 20px 18px;font-family:{_FONT_NUM};font-size:12px;line-height:1.6;font-weight:400;color:#93a09b;">{_esc(caption)}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>"""


def _order_block_html(result: VCResult, capital_usd: float | None) -> str:
    """Ordre proposé — uniquement si la recommandation est actionnable (BUY/SELL)."""
    if not result.actionable:
        return ""
    rows: list[tuple[str, str, bool]] = [("Recommandation", _esc(result.recommandation), False)]
    if result.recommandation == "BUY":
        rows.append(("Taille suggérée", _esc(f"{result.taille_pct:.1f}% du capital"), False))
        if capital_usd and capital_usd > 0:
            position = capital_usd * result.taille_pct / 100
            rows.append((
                "Capital client → position",
                f"{_esc(f'${capital_usd:,.0f}')}&nbsp;&rarr;&nbsp;{_esc(f'${position:,.0f}')}",
                False,
            ))
    rows.append(("Entrée", _esc(result.entree), False))
    rows.append(("Invalidation", _esc(result.invalidation), False))
    rows.append(("Cible", _esc(result.cible), True))

    row_html = []
    for i, (label, value, is_target) in enumerate(rows):
        border = "border-bottom:1px solid #e7deca;" if i < len(rows) - 1 else ""
        value_color = "#8a6d1f" if is_target else _INK_WARM
        row_html.append(
            f"""<tr>
          <td style="padding:12px 2px;{border}font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_MUTE_WARM};">{label}</td>
          <td align="right" style="padding:12px 2px;{border}font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:600;color:{value_color};">{value}</td>
        </tr>"""
        )

    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:34px 44px 8px;">
      {_section_header("Ordre proposé")}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">
        {"".join(row_html)}
      </table>
      <div style="margin-top:8px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Proposition soumise à validation humaine avant toute exécution.</div>
    </td>
  </tr>"""


def _dollar_potential_html(result: VCResult, capital_usd: float | None) -> str:
    """Potentiel en $ — uniquement si un capital est fourni ET upside/downside estimables.

    Jamais de montant fabriqué : sans capital renseigné ou sans upside/downside
    sourçables, la section est omise en totalité.
    """
    if not (
        capital_usd
        and capital_usd > 0
        and result.recommandation == "BUY"
        and result.taille_pct > 0
        and result.upside_pct is not None
        and result.downside_pct is not None
    ):
        return ""
    position = capital_usd * result.taille_pct / 100
    gain = position * result.upside_pct / 100
    loss = position * result.downside_pct / 100
    total = gain + loss
    gain_pct = max(0, min(100, round(gain / total * 100))) if total > 0 else 50
    loss_pct = 100 - gain_pct
    target_value = position + gain

    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:34px 44px 6px;">
      {_section_header("Potentiel en $")}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
        <tr>
          <td style="font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:400;color:{_INK_WARM};">Position <span style="font-weight:600;">{_esc(f'${position:,.0f}')}</span> &rarr; <span style="font-weight:600;">{_esc(f'${target_value:,.0f}')}</span> à la cible</td>
          <td align="right" style="font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:600;color:#8a6d1f;white-space:nowrap;">+{_esc(f'${gain:,.0f}')}</td>
        </tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;border:1px solid #e2d8bd;border-radius:6px;background-color:#efe8d6;">
        <tr>
          <td width="{gain_pct}%" style="border-radius:5px 0 0 5px;background-color:{_GOLD};background-image:linear-gradient(90deg,{_GOLD_DEEP},{_GOLD_LIGHT} 80%,{_GOLD});padding:7px 12px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#10131f;">Gain&nbsp;+{_esc(f'${gain:,.0f}')}</td>
          <td width="{100 - gain_pct}%" style="font-size:0;line-height:0;">&nbsp;</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:18px;">
        <tr>
          <td style="font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:400;color:{_INK_WARM};">Risque encaissé si invalidation (<span style="font-weight:600;">{_esc(result.invalidation)}</span>)</td>
          <td align="right" style="font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:600;color:{_INK_WARM};white-space:nowrap;">&minus;{_esc(f'${loss:,.0f}')}</td>
        </tr>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;border:1px solid #e2d8bd;border-radius:6px;background-color:#efe8d6;">
        <tr>
          <td width="{loss_pct}%" style="border-radius:5px 0 0 5px;background-color:{_RUST};padding:7px 12px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_IVORY};white-space:nowrap;">&minus;{_esc(f'${loss:,.0f}')}</td>
          <td width="{100 - loss_pct}%" style="font-size:0;line-height:0;">&nbsp;</td>
        </tr>
      </table>
      <div style="margin-top:8px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Échelle commune : barres proportionnelles aux montants en $.</div>
    </td>
  </tr>"""


def _scenario_card_html(sc: dict, width: str, padding: str) -> str:
    nom = str(sc.get("nom", ""))
    titre, bar_style, is_center = _SCEN_META.get(nom, ("Scénario", f"background-color:{_GOLD};", False))
    cible = _esc(sc.get("cible", ""))
    proba = max(0, min(100, int(sc.get("probabilite") or 0)))
    conf = _esc(sc.get("confiance", "faible"))
    border = _GOLD if is_center else "#e0d6ba"
    bg = "#fdfaf1" if is_center else "#fbf8f0"
    return f"""<td class="stack sc-card" width="{width}" style="padding:{padding};vertical-align:top;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {border};border-radius:10px;background-color:{bg};">
              <tr><td style="padding:16px 16px 14px;">
                <div style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_MUTE_WARM};">{_esc(titre)}</div>
                <div style="padding-top:8px;font-family:{_FONT_NUM};font-size:22px;line-height:1.2;font-weight:600;letter-spacing:-0.01em;font-variant-numeric:lining-nums tabular-nums;color:{_INK_WARM};">{cible}</div>
                <div style="padding-top:10px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_MUTE_WARM};">Probabilité&nbsp;{proba}%</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;background-color:#ece4cf;border-radius:4px;"><tr>
                  <td width="{proba}%" style="height:6px;line-height:6px;font-size:0;border-radius:4px;{bar_style}">&nbsp;</td>
                  <td width="{100 - proba}%" style="height:6px;line-height:6px;font-size:0;">&nbsp;</td>
                </tr></table>
                <div style="padding-top:10px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Confiance {conf}</div>
              </td></tr>
            </table>
          </td>"""


def _scenarios_block_html(scenarios: list[dict]) -> str:
    if not scenarios:
        return ""
    n = len(scenarios)
    if n == 3:
        widths = ["33%", "34%", "33%"]
        paddings = ["0 6px 0 0", "0 3px", "0 0 0 6px"]
    else:
        share = f"{100 // n}%"
        widths = [share] * n
        paddings = ["0 4px"] * n
    cards = "".join(
        _scenario_card_html(sc, widths[i], paddings[i]) for i, sc in enumerate(scenarios)
    )
    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:34px 44px 2px;">
      {_section_header("Scénarios")}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
        <tr>{cards}</tr>
      </table>
    </td>
  </tr>"""


def _methodology_block_html() -> str:
    rows = []
    for i, (name, desc) in enumerate(_METHODOLOGY_SOURCES):
        border = "border-bottom:1px solid #ece3cd;" if i < len(_METHODOLOGY_SOURCES) - 1 else ""
        rows.append(
            f"""<tr>
          <td style="padding:8px 2px;{border}font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_MUTE_WARM};white-space:nowrap;">{_esc(name)}</td>
          <td align="right" style="padding:8px 2px;{border}font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:400;color:{_INK_WARM};">{_esc(desc)}</td>
        </tr>"""
        )
    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:32px 44px 6px;">
      {_section_header("Méthodologie & sources")}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">
        {"".join(rows)}
      </table>
      <div style="margin-top:16px;padding:2px 0 2px 16px;border-left:2px solid {_EMERALD};font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Principe méthodologique&nbsp;: aucune donnée absente n'est estimée.</div>
    </td>
  </tr>"""


def _gaps_block_html(gaps: list[str]) -> str:
    if not gaps:
        return ""
    items = "".join(
        f"""<tr>
                <td width="16" style="vertical-align:top;padding-top:4px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;color:#8a6d1f;">&#9671;</td>
                <td style="font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:400;color:{_INK_WARM};">{_esc(g)}</td>
              </tr>"""
        for g in gaps
    )
    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:24px 44px 6px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px dashed #c8b878;border-radius:10px;background-color:#faf6ea;">
        <tr>
          <td style="padding:18px 20px;">
            <div style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_MUTE_WARM};">Données insuffisantes&nbsp;: non estimées</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
              {items}
            </table>
            <div style="margin-top:10px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Conformément à notre principe&nbsp;: aucune donnée absente n'est estimée.</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>"""


def _fallback_note_html(result: VCResult) -> str:
    if result.llm_used:
        return ""
    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:24px 44px 6px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px dashed #c9603c;border-radius:10px;background-color:#faf0ea;">
        <tr>
          <td style="padding:16px 20px;font-family:{_FONT_SANS};font-size:13px;line-height:1.6;color:#8a3a1f;">
            <strong>Analyse qualitative LLM indisponible</strong>. Ce rapport repose uniquement sur les signaux quantitatifs.
          </td>
        </tr>
      </table>
    </td>
  </tr>"""


def _ta_block_html(result: VCResult) -> str:
    """Section « Analyse technique » : niveaux dérivés de l'OHLCV réel + graphique.

    Data-gated : vide si aucune donnée technique n'a été dérivée (comportement
    identique à aujourd'hui). Chaque niveau porte sa base factuelle (facts-only) ;
    le graphique est un PNG data-URI email-safe déjà produit en amont.
    """
    if not result.ta_levels_lines and not result.chart_data_uri:
        return ""

    trend = f"&nbsp;&middot;&nbsp;tendance {_esc(result.ta_trend)}" if result.ta_trend else ""
    tf = _esc(result.ta_timeframe) if result.ta_timeframe else ""

    lines_html = ""
    if result.ta_levels_lines:
        items = "".join(
            f'<li style="margin:0 0 6px;">{_esc(line)}</li>' for line in result.ta_levels_lines
        )
        lines_html = (
            f'<ul style="margin:14px 0 0;padding-left:18px;font-family:{_FONT_SANS};'
            f'font-size:14px;line-height:1.6;color:{_INK_WARM};">{items}</ul>'
        )

    chart_html = ""
    if result.chart_data_uri.startswith("data:image/png"):
        cap = f"Bougies {tf}" if tf else "Bougies OHLCV"
        chart_html = (
            f'<div style="margin-top:16px;"><img src="{result.chart_data_uri}" '
            f'alt="Graphique {cap} avec niveaux d&eacute;riv&eacute;s" width="560" '
            f'style="display:block;width:100%;max-width:560px;height:auto;border-radius:8px;'
            f'border:1px solid rgba(201,162,39,0.25);" /></div>'
            f'<div style="margin-top:6px;font-family:{_FONT_MONO};font-size:10px;color:{_MUTE_WARM};">'
            f'{_esc(cap)}&nbsp;&middot;&nbsp;niveaux d&eacute;riv&eacute;s des donn&eacute;es, jamais fabriqu&eacute;s</div>'
        )

    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:30px 44px 6px;">
      {_section_header("Analyse technique")}
      <div style="margin-top:6px;font-family:{_FONT_MONO};font-size:11px;letter-spacing:0.04em;color:{_MUTE_WARM};">OHLCV r&eacute;el{trend}</div>
      {lines_html}
      {chart_html}
    </td>
  </tr>"""


def _fmt_compact_usd(value: float) -> str:
    """Capitalisation en format compact lisible ($30M, $1.2B). Facts-only."""
    v = float(value)
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B".replace(".0B", "B")
    if v >= 1_000_000:
        return f"${v / 1_000_000:.0f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


def _roi_block_html(result: VCResult) -> str:
    """Section « Projection par comparables » : placement du token dans l'histoire.

    Data-gated : vide sans scénario (capitalisation actuelle inconnue). Chaque
    ligne est un PLACEMENT tangible (« à la capitalisation d'un comparable, Nx »),
    JAMAIS une cible ni une promesse. L'avertissement du dôme est affiché en clair.
    """
    if not result.roi_scenarios:
        return ""

    basis_label = "FDV" if result.roi_basis == "fdv" else "capitalisation"
    sector = _esc(result.roi_sector) if result.roi_sector else ""
    if result.roi_sector_recognized and sector:
        sector_line = f"Secteur&nbsp;: {sector}"
    else:
        sector_line = "Secteur non reconnu&nbsp;: comparables g&eacute;n&eacute;riques"

    rows = ""
    for s in result.roi_scenarios:
        label = _esc(s.get("label", ""))
        ref = _fmt_compact_usd(s.get("ref_mcap_usd", 0))
        mult = s.get("multiple", 0)
        note = _esc(s.get("note", ""))
        note_html = (
            f'<div style="font-family:{_FONT_SANS};font-size:11px;color:{_MUTE_WARM};'
            f'margin-top:2px;">{note}</div>'
            if note
            else ""
        )
        rows += (
            f'<tr>'
            f'<td style="padding:9px 0;border-bottom:1px solid rgba(201,162,39,0.18);'
            f'font-family:{_FONT_SANS};font-size:14px;color:{_INK_WARM};">'
            f'{label}<div style="font-family:{_FONT_MONO};font-size:11px;color:{_MUTE_WARM};'
            f'margin-top:2px;">{basis_label} de r&eacute;f&eacute;rence {ref}</div>{note_html}</td>'
            f'<td align="right" style="padding:9px 0;border-bottom:1px solid rgba(201,162,39,0.18);'
            f'font-family:{_FONT_MONO};font-size:18px;font-weight:700;color:{_GOLD_DEEP};'
            f'white-space:nowrap;">{mult:g}x</td>'
            f'</tr>'
        )

    disclaimer = _esc(
        result.roi_disclaimer
        or "Placement historique par comparables, pas une prevision ni une cible."
    )

    return f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:30px 44px 6px;">
      {_section_header("Projection par comparables")}
      <div style="margin-top:6px;font-family:{_FONT_MONO};font-size:11px;letter-spacing:0.04em;color:{_MUTE_WARM};">{sector_line}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">
        {rows}
      </table>
      <div style="margin-top:12px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">{disclaimer}</div>
    </td>
  </tr>"""


def render_html_report(
    result: VCResult,
    *,
    generated_at: str,
    recipient: str | None = None,
    report_number: int | None = None,
    series_number: int | None = None,
    capital_usd: float | None = None,
    tier: str = "premium",
) -> str:
    """Document HTML autonome, CSS inline — prêt pour l'email (et le futur site).

    Design « B4 » : hero sombre nocturne or/émeraude (goutte dorée + wordmark ARIA)
    au-dessus d'un corps ivoire et d'un pied « certificat ». Chaque valeur du hero
    (titre, badges, R/R…) et du corps (thèse, scénarios, ordre…) provient de
    ``result`` — jamais codée en dur — et est HTML-échappée avant injection.

    ``recipient`` (optionnel) inscrit un filigrane d'édition personnelle : une
    fuite du rapport devient traçable au destinataire. Une empreinte SHA-256 du
    contenu est apposée en pied (anti-falsification). ``report_number``
    (optionnel) affiche « Rapport n°N » : un abonné recevant plusieurs analyses
    suivies du même token doit pouvoir les distinguer d'un coup d'œil.
    ``series_number`` (optionnel) affiche « Série 00.0NN » : compteur global de
    toutes les analyses ARIA, tous tokens confondus — identité d'édition numérotée.
    ``capital_usd`` (optionnel) convertit la taille suggérée en montants en dollars
    dans l'ordre proposé et dans la section « Potentiel en $ » — omise sans capital.
    ``tier`` (« premium » par défaut, ou « standard ») sélectionne l'édition : le
    design (or, émeraude, corps ivoire, structure) est identique, seules les
    surfaces sombres (hero, pied « certificat », bandes) changent de teinte et le
    tier « standard » masque l'analyse détaillée, la méthodologie et les
    références (réservées au premium). Toute valeur hors ``{"standard"}`` retombe
    sur « premium » (défaut sûr, cf. ``_theme`` — allowlist fermée).
    """
    theme = _theme(tier)
    is_standard = theme["tier"] == _TIER_STANDARD
    ref_id, full_hash = report_integrity(result, generated_at=generated_at, recipient=recipient)
    title = _report_title(result)

    # Ligne méta du hero : chaque segment optionnel n'apparaît que si fourni.
    meta_parts = []
    if series_number:
        meta_parts.append(f"Série {_esc(_format_serial(series_number))}")
    if report_number:
        meta_parts.append(f"Rapport n°{_esc(report_number)}")
    meta_parts.append(f"Généré le {_esc(generated_at)}")
    meta_line = " &middot; ".join(meta_parts) + "&nbsp;&nbsp;&middot;&nbsp;&nbsp;Émis par ARIA&nbsp;Vanguard&nbsp;ZHC"

    # Préheader invisible (aperçu client mail) — jamais de valeur inventée (R/R omis si non estimable).
    preheader = f"{_esc(title)} (Base) &middot; {_esc(result.recommandation)}"
    if result.rr is not None:
        preheader += f" &middot; R/R {_esc(f'{result.rr:.1f}')}"
    if result.upside_pct is not None and result.downside_pct is not None:
        preheader += (
            f" &middot; Upside +{_esc(f'{result.upside_pct:.0f}')}% / "
            f"Downside &minus;{_esc(f'{result.downside_pct:.0f}')}%"
        )
    preheader += " &middot; Note de recherche ARIA Vanguard ZHC"

    badges_html = _badges_html(result)
    rr_block = _rr_block_html(result)

    # ── Corps ivoire : blocs conditionnels, chacun omis si la donnée sous-jacente est absente. ──
    fallback_note = _fallback_note_html(result)

    tldr_block = ""
    if result.resume_executif:
        tldr_block = f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:38px 44px 8px;">
      {_section_header("En bref")}
      <div class="ink" style="margin-top:16px;padding:2px 0 2px 18px;border-left:3px solid {_GOLD};font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-style:italic;color:{_INK_WARM};">&laquo;&nbsp;{_esc(result.resume_executif)}&nbsp;&raquo;</div>
    </td>
  </tr>"""

    order_block = _order_block_html(result, capital_usd)
    dollar_block = _dollar_potential_html(result, capital_usd)

    these_block = ""
    if result.these:
        these_block = f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:34px 44px 4px;">
      {_section_header("Thèse d'investissement")}
      <div class="ink" style="margin-top:14px;font-family:{_FONT_SANS};font-size:14px;line-height:1.6;font-weight:400;color:{_INK_WARM};">{_esc(result.these)}</div>
    </td>
  </tr>"""

    scenarios_block = _scenarios_block_html(result.scenarios)

    # Analyse technique (niveaux OHLCV réels + graphique) : profondeur réservée au
    # premium, comme l'analyse détaillée. En standard, entièrement omise (data-gated).
    ta_block = "" if is_standard else _ta_block_html(result)

    # Projection ROI par comparables historiques (Voûte 3, tâche #5) : contexte
    # tangible réservé au premium, JAMAIS une cible ni un montant inventé. Data-gated :
    # vide si la capitalisation actuelle est inconnue.
    roi_block = "" if is_standard else _roi_block_html(result)

    watermark_diagonal = ""
    if recipient:
        watermark_diagonal = f"""<tr>
    <td class="ivory" align="center" style="background-color:{_IVORY};padding:26px 24px 6px;">
      <div style="transform:rotate(-4deg);-webkit-transform:rotate(-4deg);font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#e3d9bd;">Édition&nbsp;personnelle&nbsp;&middot;&nbsp;{_esc(recipient)}&nbsp;&middot;&nbsp;{_esc(ref_id)}</div>
    </td>
  </tr>"""

    gaps_block = _gaps_block_html(result.donnees_insuffisantes)

    # Analyse détaillée / méthodologie / références : réservées au tier premium.
    # En standard, ces sections sont entièrement omises (jamais tronquées ni
    # partiellement affichées) et remplacées par une ligne d'incitation statique.
    if is_standard:
        detailed_block = f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:22px 44px 40px;">
      <div style="font-family:{_FONT_SANS};font-size:12px;line-height:1.6;font-style:italic;color:{_MUTE_WARM};">Analyse détaillée, méthodologie et sources&nbsp;: réservées à l'édition Premium.</div>
    </td>
  </tr>"""
        methodology_row = ""
        references_row = ""
    else:
        body_html = _render_markdown_body(result.rapport_detaille)
        detailed_block = f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:32px 44px 6px;">
      {_section_header("Analyse détaillée")}
      <div style="margin-top:18px;">{body_html}</div>
    </td>
  </tr>"""
        methodology_row = _methodology_block_html()
        references_html = _references_block(result.liens_projet)
        references_row = f"""<tr>
    <td class="ivory pad" style="background-color:{_IVORY};padding:12px 44px 40px;">
      {references_html}
    </td>
  </tr>"""

    emblem = _emblem_data_uri()
    footer_emblem_img = (
        f"<img src='{emblem}' width='40' height='40' alt='' style='display:block;margin:0 auto 10px;border-radius:8px;'>"
        if emblem else ""
    )
    footer_watermark = f"Édition personnelle&nbsp;&middot;&nbsp;{_esc(recipient)}" if recipient else ""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>ARIA Vanguard ZHC · Note de recherche · {_esc(title)}</title>
<style>
  @media (max-width:480px){{
    .stack{{display:block !important;width:100% !important;box-sizing:border-box !important;}}
    .pad{{padding-left:22px !important;padding-right:22px !important;}}
    .hero-pad{{padding-left:22px !important;padding-right:22px !important;padding-top:28px !important;}}
    .sc-card{{padding:0 0 14px 0 !important;}}
    .kpi{{margin:3px 2px !important;padding:8px 12px !important;}}
    .meta-right{{text-align:left !important;padding-top:6px !important;}}
    .microprint{{display:none !important;}}
  }}
  @media (prefers-color-scheme:dark){{
    .ivory{{background-color:#f6f2e9 !important;}}
    .ink{{color:#2a2620 !important;}}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:{_NIGHT};-webkit-text-size-adjust:100%;">

<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{preheader}</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_NIGHT};">
<tr>
<td align="center" style="padding:34px 12px 46px;">

  <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:640px;width:100%;">

  <tr>
    <td style="height:9px;line-height:9px;font-size:0;border-radius:14px 14px 0 0;border-top:1px solid {_GOLD_DEEP};border-bottom:1px solid rgba(15,107,92,0.65);background-color:{theme['dark_base']};background-image:linear-gradient(90deg, rgba(176,134,43,0.55), rgba(230,196,99,0.75) 30%, rgba(31,138,116,0.55) 70%, rgba(15,107,92,0.45));">&nbsp;</td>
  </tr>

  <tr>
    <td class="hero-pad" style="{theme['hero_bg']}padding:33px 44px 30px;">

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;"><a href="https://ariavanguardzhc.com" style="color:{_GOLD_SOFT};text-decoration:none;letter-spacing:0.04em;">ariavanguardzhc.com</a></td>
          <td align="right" style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD_LIGHT};white-space:nowrap;">Confidentiel</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:10px;">
        <tr>
          <td align="left">
            <span style="display:inline-block;padding:5px 12px;border-radius:999px;{theme['pill_style']}font-family:{_FONT_SANS};font-size:10px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;line-height:1;">{_esc(theme['label'])}</span>
          </td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;">
        <tr>
          <td style="font-family:{_FONT_MONO};font-size:11px;line-height:1.6;color:#93a09b;white-space:nowrap;">{_esc(ref_id)}</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:30px;">
        <tr>
          <td align="center">
            <div style="width:1px;height:20px;margin:0 auto;background-image:linear-gradient(to bottom, rgba(230,196,99,0), {_GOLD_LIGHT});font-size:0;line-height:0;">&nbsp;</div>
            <div style="width:0;height:0;margin:2px auto 0;border-left:7px solid transparent;border-right:7px solid transparent;border-bottom:9px solid {_GOLD_LIGHT};font-size:0;line-height:0;">&nbsp;</div>
            <div style="width:0;height:0;margin:0 auto;border-left:7px solid transparent;border-right:7px solid transparent;border-top:9px solid {_GOLD_DEEP};font-size:0;line-height:0;">&nbsp;</div>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:16px;">
            <div class="wordmark" style="font-family:{_FONT_SERIF};font-size:40px;line-height:1;font-weight:700;color:{_GOLD_LIGHT};letter-spacing:16px;margin-right:-16px;text-align:center;">ARIA</div>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:10px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="width:44px;"><div style="height:1px;background-image:linear-gradient(to right, rgba(201,162,39,0), {_GOLD});font-size:0;line-height:1px;">&nbsp;</div></td>
                <td style="padding:0 12px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD_LIGHT};white-space:nowrap;">Vanguard&nbsp;&middot;&nbsp;ZHC</td>
                <td style="width:44px;"><div style="height:1px;background-image:linear-gradient(to left, rgba(201,162,39,0), {_GOLD});font-size:0;line-height:1px;">&nbsp;</div></td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:34px;">
        <tr>
          <td align="center" style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD_LIGHT};">Note de recherche&nbsp;&middot;&nbsp;Analyse d&rsquo;investissement</td>
        </tr>
        <tr>
          <td align="center" style="padding-top:12px;">
            <div class="h1" style="font-family:{_FONT_SERIF};font-size:40px;line-height:1.15;font-weight:600;color:#f2ead8;letter-spacing:1px;">{_esc(title)}</div>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:10px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD_LIGHT};">RÉSEAU BASE</td>
        </tr>
        <tr>
          <td align="center" style="padding-top:14px;">
            <div style="display:inline-block;padding:8px 14px;border:1px solid rgba(201,162,39,0.4);border-radius:6px;background-color:rgba(7,10,18,0.55);font-family:{_FONT_MONO};font-size:11px;line-height:1.6;color:#93a09b;word-break:break-all;">{_esc(result.contract)}</div>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:16px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;color:#93a09b;">{meta_line}</td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:24px;">
        <tr>
          <td align="center">
            {badges_html}
          </td>
        </tr>
      </table>

      {rr_block}

    </td>
  </tr>

  <tr>
    <td class="microprint" style="background-color:{theme['deep_base']};border-top:1px solid rgba(201,162,39,0.35);border-bottom:1px solid rgba(15,107,92,0.5);padding:4px 10px;overflow:hidden;">
      <div style="white-space:nowrap;overflow:hidden;font-family:{_FONT_SANS};font-size:7px;line-height:1.6;letter-spacing:0.08em;text-transform:uppercase;color:rgba(230,196,99,0.42);">ARIA&nbsp;Vanguard&nbsp;ZHC&nbsp;&middot;&nbsp;Note&nbsp;certifi&eacute;e&nbsp;{_esc(ref_id)}&nbsp;&middot;&nbsp;Reproduction&nbsp;interdite&nbsp;&middot;&nbsp;ARIA&nbsp;Vanguard&nbsp;ZHC&nbsp;&middot;&nbsp;Note&nbsp;certifi&eacute;e&nbsp;{_esc(ref_id)}&nbsp;&middot;&nbsp;Reproduction&nbsp;interdite&nbsp;&middot;&nbsp;ARIA&nbsp;Vanguard&nbsp;ZHC&nbsp;&middot;&nbsp;Note&nbsp;certifi&eacute;e&nbsp;{_esc(ref_id)}</div>
    </td>
  </tr>

  {fallback_note}
  {tldr_block}
  {order_block}
  {dollar_block}
  {these_block}
  {scenarios_block}
  {watermark_diagonal}
  {gaps_block}

  {ta_block}

  {roi_block}

  {detailed_block}

  {methodology_row}

  {references_row}

  <tr>
    <td style="height:8px;line-height:8px;font-size:0;border-top:1px solid rgba(15,107,92,0.6);border-bottom:1px solid {_GOLD_DEEP};background-color:{theme['dark_base']};background-image:linear-gradient(90deg, rgba(15,107,92,0.45), rgba(31,138,116,0.55) 30%, rgba(230,196,99,0.75) 70%, rgba(176,134,43,0.55));">&nbsp;</td>
  </tr>
  <tr>
    <td class="pad" style="background-color:{theme['dark_base']};background-image:linear-gradient(180deg,{theme['footer_mid']},{theme['deep_base']});padding:30px 44px 26px;border-radius:0 0 14px 14px;">

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td align="center">
            {footer_emblem_img}
            <span style="font-family:{_FONT_SERIF};font-size:17px;line-height:1.3;font-weight:700;letter-spacing:8px;margin-right:-8px;color:{_GOLD_LIGHT};">ARIA</span>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:6px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:{_GOLD_LIGHT};">Vanguard&nbsp;&middot;&nbsp;ZHC</td>
        </tr>
      </table>

      <div style="margin-top:18px;height:1px;background-image:linear-gradient(to right, rgba(201,162,39,0), rgba(201,162,39,0.45) 35%, rgba(31,138,116,0.45) 65%, rgba(31,138,116,0));font-size:0;line-height:1px;">&nbsp;</div>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:16px;">
        <tr>
          <td class="stack" style="font-family:{_FONT_MONO};font-size:11px;line-height:1.6;color:#93a09b;">RÉF.&nbsp;{_esc(ref_id)}</td>
          <td class="stack meta-right" align="right" style="font-family:{_FONT_SANS};font-size:11px;line-height:1.6;color:#93a09b;">{footer_watermark}</td>
        </tr>
      </table>
      <div style="margin-top:6px;font-family:{_FONT_MONO};font-size:11px;line-height:1.6;color:#93a09b;word-break:break-all;">Daté du {_esc(generated_at)} &middot; Empreinte SHA-256&nbsp;: {_esc(full_hash)}</div>

      <div style="margin-top:16px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;font-weight:400;color:#93a09b;">Cette note constitue une <span style="font-weight:600;color:#f2ead8;">proposition soumise à validation humaine</span>&nbsp;: aucune exécution automatique n&rsquo;est engagée. Elle ne constitue pas un conseil en investissement. Les crypto-actifs présentent un risque de perte totale du capital investi.</div>

      <div class="microprint" style="margin-top:14px;overflow:hidden;white-space:nowrap;font-family:{_FONT_SANS};font-size:7px;line-height:1.6;letter-spacing:0.08em;text-transform:uppercase;color:rgba(230,196,99,0.35);">ARIA&nbsp;Vanguard&nbsp;ZHC&nbsp;&middot;&nbsp;Document&nbsp;confidentiel&nbsp;&middot;&nbsp;{_esc(ref_id)}&nbsp;&middot;&nbsp;Reproduction&nbsp;et&nbsp;revente&nbsp;interdites&nbsp;&middot;&nbsp;ARIA&nbsp;Vanguard&nbsp;ZHC&nbsp;&middot;&nbsp;Document&nbsp;confidentiel&nbsp;&middot;&nbsp;{_esc(ref_id)}&nbsp;&middot;&nbsp;Reproduction&nbsp;et&nbsp;revente&nbsp;interdites&nbsp;&middot;&nbsp;ARIA&nbsp;Vanguard&nbsp;ZHC</div>

      <div style="margin-top:10px;font-family:{_FONT_SANS};font-size:11px;line-height:1.6;color:#93a09b;" align="center">&copy;&nbsp;2026 ARIA Vanguard ZHC&nbsp;&middot; Tous droits réservés&nbsp;&middot; Document confidentiel&nbsp;: reproduction et revente interdites.</div>
    </td>
  </tr>

  </table>

</td>
</tr>
</table>

</body>
</html>"""
