"""Rendu graphique email-safe d'un cours (Pillow → PNG en data-URI base64).

Gmail et la plupart des clients mail strippent le SVG : le **seul** format
d'image vraiment portable en email est un PNG inline en ``data:`` URI. Ce module
dessine, avec Pillow (déjà dépendance du projet — cf. ``vc_report`` qui embarque
l'emblème de la même façon), un petit graphique de prix sobre et le renvoie en
``data:image/png;base64,...`` — aucune ressource externe, aucun appel réseau,
entièrement déterministe (mêmes bougies → même PNG).

Palette cohérente avec le rapport B4 (corps ivoire, or / émeraude / encre) :
- ligne de prix : encre chaude ;
- entrée : émeraude ; invalidation : rouille ; cible : or.

Une série vide est gérée sans lever : un PNG neutre aux dimensions demandées est
renvoyé (jamais d'image cassée dans l'email).
"""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw

from aria_core.skills.ta_levels import Candle

# ─── Palette (alignée sur vc_report — corps ivoire or/émeraude/encre) ───
_BG = (246, 242, 233)          # ivoire (_IVORY)
_INK = (42, 38, 32)            # encre chaude (_INK_WARM) — ligne de prix
_GRID = (226, 216, 189)        # filet discret
_AXIS = (198, 184, 120)        # cadre or pâle
_EMERALD = (31, 138, 116)      # entrée
_RUST = (163, 74, 42)          # invalidation
_GOLD = (176, 134, 43)         # cible
_MUTE = (122, 114, 100)        # texte neutre

_PAD_L = 10
_PAD_R = 10
_PAD_T = 14
_PAD_B = 14
_MIN_W = 80
_MIN_H = 40
# Seuil sous lequel une série est considérée « plate » (amplitude nulle).
_EPS_RANGE = 1e-9


def _data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _neutral_png(width: int, height: int) -> str:
    """PNG neutre (fond ivoire + cadre) — utilisé quand il n'y a rien à tracer."""
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=_AXIS, width=1)
    draw.line([_PAD_L, height // 2, width - _PAD_R, height // 2], fill=_GRID, width=1)
    return _data_uri(img)


def _dashed_hline(
    draw: ImageDraw.ImageDraw,
    x0: int,
    x1: int,
    y: int,
    color: tuple[int, int, int],
    *,
    dash: int = 6,
    gap: int = 4,
    width: int = 1,
) -> None:
    """Trace une ligne horizontale pointillée (repère de niveau) de x0 à x1."""
    x = x0
    while x < x1:
        seg = min(dash, x1 - x)
        draw.line([x, y, x + seg, y], fill=color, width=width)
        x += dash + gap


def render_price_chart_png(
    candles: list[Candle],
    *,
    entry: float | None = None,
    invalidation: float | None = None,
    target: float | None = None,
    width: int = 560,
    height: int = 220,
) -> str:
    """Dessine un graphique en ligne du close et renvoie un PNG en data-URI.

    Les lignes horizontales ``entry`` / ``invalidation`` / ``target`` sont
    tracées si fournies (émeraude / rouille / or). L'échelle verticale intègre
    ces niveaux pour qu'ils restent toujours visibles. ``candles`` vide → PNG
    neutre aux dimensions demandées (aucune exception, aucune ressource externe).
    Déterministe et hors-ligne.
    """
    width = max(_MIN_W, int(width))
    height = max(_MIN_H, int(height))

    if not candles:
        return _neutral_png(width, height)

    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]

    # Bornes verticales : amplitude des bougies + niveaux fournis (toujours visibles).
    extra = [v for v in (entry, invalidation, target) if v is not None]
    pmin = min(lows + extra)
    pmax = max(highs + extra)
    if pmax - pmin < _EPS_RANGE:
        # Série plate : on ouvre un petit intervalle artificiel autour du prix.
        mid = pmax
        pmin, pmax = mid - 1.0, mid + 1.0
    margin = (pmax - pmin) * 0.06
    pmin -= margin
    pmax += margin
    prange = pmax - pmin

    plot_l = _PAD_L
    plot_r = width - _PAD_R
    plot_t = _PAD_T
    plot_b = height - _PAD_B
    plot_w = max(1, plot_r - plot_l)
    plot_h = max(1, plot_b - plot_t)

    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    # Cadre + ligne médiane (repères sobres).
    draw.rectangle([0, 0, width - 1, height - 1], outline=_AXIS, width=1)
    draw.line([plot_l, (plot_t + plot_b) // 2, plot_r, (plot_t + plot_b) // 2], fill=_GRID, width=1)

    def _y(price: float) -> int:
        return int(round(plot_b - (price - pmin) / prange * plot_h))

    def _x(i: int) -> int:
        n = len(closes)
        if n == 1:
            return (plot_l + plot_r) // 2
        return int(round(plot_l + i / (n - 1) * plot_w))

    # Niveaux (tracés sous la courbe de prix pour rester lisibles).
    for value, color in (
        (target, _GOLD),
        (invalidation, _RUST),
        (entry, _EMERALD),
    ):
        if value is None:
            continue
        y = _y(float(value))
        if plot_t <= y <= plot_b:
            _dashed_hline(draw, plot_l, plot_r, y, color, width=2)

    # Courbe de prix (close) — encre.
    if len(closes) == 1:
        cx, cy = _x(0), _y(closes[0])
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=_INK)
    else:
        points = [(_x(i), _y(closes[i])) for i in range(len(closes))]
        draw.line(points, fill=_INK, width=2, joint="curve")

    return _data_uri(img)
