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


# ─── Thème « terminal » sombre (look DexScreener) pour le carnet de bord ───
_DX_BG = (13, 17, 23)          # fond quasi noir
_DX_GRID = (30, 37, 47)        # grille discrète
_DX_UP = (38, 166, 91)         # bougie haussière (vert)
_DX_DOWN = (216, 68, 68)       # bougie baissière (rouge)
_DX_WICK_UP = (46, 189, 106)
_DX_WICK_DOWN = (230, 84, 84)
_DX_TEXT = (139, 148, 158)     # texte gris
_DX_ENTRY = (88, 166, 255)     # entrée (bleu)
_DX_TARGET = (210, 168, 60)    # cible (or)
_DX_INVAL = (240, 100, 90)     # invalidation (rouge clair)
_DX_SIM = (120, 130, 145)      # zone de simulation


def render_scenario_png(
    candles: list[Candle],
    *,
    entry: float | None = None,
    invalidation: float | None = None,
    target: float | None = None,
    markers: list | None = None,
    horizon_weeks: int = 4,
    width: int = 680,
    height: int = 300,
) -> str:
    """Chandeliers RÉELS (look DexScreener) + **simulation** forward.

    À gauche : les VRAIES bougies japonaises (OHLC, vert/rouge) sur fond sombre, comme
    un screenshot DexScreener. À droite : la SIMULATION sur ~``horizon_weeks`` semaines —
    scénario haussier (vers la cible) et baissier (vers l'invalidation) depuis le point
    d'entrée, en zone ombrée + chemins pointillés. C'est une **simulation** clairement
    étiquetée, jamais une prévision (dôme). Déterministe, hors-ligne. Vide -> PNG neutre.
    """
    width = max(_MIN_W, int(width))
    height = max(_MIN_H, int(height))
    if not candles:
        img = Image.new("RGB", (width, height), _DX_BG)
        return _data_uri(img)

    opens = [float(c.open) for c in candles]
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    extra = [v for v in (entry, invalidation, target) if v is not None]
    pmin = min(lows + extra)
    pmax = max(highs + extra)
    if pmax - pmin < _EPS_RANGE:
        mid = pmax
        pmin, pmax = mid - 1.0, mid + 1.0
    margin = (pmax - pmin) * 0.08
    pmin -= margin
    pmax += margin
    prange = pmax - pmin

    pad_r = 50  # colonne de prix à droite (comme DexScreener)
    plot_l, plot_r = 8, width - pad_r
    top_area = 20
    # Panneau prix (haut ~72%) + panneau volume (bas ~18%) + marge.
    price_t = top_area
    vol_h = int((height - top_area - 14) * 0.20)
    price_b = height - 14 - vol_h - 6
    vol_t = price_b + 6
    vol_b = height - 14
    plot_h = max(1, price_b - price_t)
    plot_w = max(1, plot_r - plot_l)
    hist_r = plot_l + int(plot_w * 0.64)  # historique 64% | simulation 36%

    img = Image.new("RGB", (width, height), _DX_BG)
    draw = ImageDraw.Draw(img)

    def _y(price: float) -> int:
        return int(round(price_b - (price - pmin) / prange * plot_h))

    # Grille horizontale + graduations de prix à droite (panneau prix).
    for k in range(5):
        gy = price_t + int(k / 4 * plot_h)
        draw.line([plot_l, gy, plot_r, gy], fill=_DX_GRID, width=1)
        price = pmax - k / 4 * prange
        draw.text((plot_r + 4, gy - 5), _fmt_axis(price), fill=_DX_TEXT)

    n = len(candles)
    step = (hist_r - plot_l) / max(1, n)
    body_w = max(1, int(step * 0.66))

    def _xc(i: int) -> int:
        return int(round(plot_l + (i + 0.5) * step))

    # Moyenne mobile (MA7) — ligne dorée discrète, comme un indicateur DexScreener.
    ma_win = min(7, n)
    if ma_win >= 2:
        ma_pts = []
        for i in range(n):
            lo = max(0, i - ma_win + 1)
            seg = closes[lo:i + 1]
            ma_pts.append((_xc(i), _y(sum(seg) / len(seg))))
        draw.line(ma_pts, fill=(120, 108, 60), width=1, joint="curve")

    # Bougies réelles (mèche + corps vert/rouge).
    for i in range(n):
        x = _xc(i)
        up = closes[i] >= opens[i]
        wick = _DX_WICK_UP if up else _DX_WICK_DOWN
        body = _DX_UP if up else _DX_DOWN
        draw.line([x, _y(highs[i]), x, _y(lows[i])], fill=wick, width=1)
        y_o, y_c = _y(opens[i]), _y(closes[i])
        top, bot = min(y_o, y_c), max(y_o, y_c)
        if bot - top < 1:
            bot = top + 1
        draw.rectangle([x - body_w // 2, top, x + body_w // 2, bot], fill=body)

    # Panneau volume (barres vert/rouge en bas).
    vols = [float(getattr(c, "volume", 0.0) or 0.0) for c in candles]
    vmax = max(vols) if vols else 0.0
    if vmax > 0:
        for i in range(n):
            x = _xc(i)
            bh = int((vols[i] / vmax) * (vol_b - vol_t))
            col = _DX_UP if closes[i] >= opens[i] else _DX_DOWN
            draw.rectangle([x - body_w // 2, vol_b - bh, x + body_w // 2, vol_b], fill=col)
    draw.line([plot_l, vol_b, plot_r, vol_b], fill=_DX_GRID, width=1)

    # Zone de simulation ombrée (à droite du dernier chandelier).
    draw.rectangle([hist_r, price_t, plot_r, price_b], fill=(18, 23, 31))
    draw.line([hist_r, price_t, hist_r, price_b], fill=_DX_GRID, width=1)

    # Niveaux (entrée / cible / invalidation) en pointillés + étiquette de prix à droite.
    for value, color, lab in (
        (target, _DX_TARGET, "cible"), (invalidation, _DX_INVAL, "inval"), (entry, _DX_ENTRY, "entree")
    ):
        if value is None:
            continue
        y = _y(float(value))
        if price_t <= y <= price_b:
            _dashed_hline(draw, plot_l, plot_r, y, color, width=1)
            draw.rectangle([plot_r, y - 6, width - 1, y + 6], fill=color)
            draw.text((plot_r + 3, y - 5), _fmt_axis(float(value)), fill=(13, 17, 23))

    # Départ de la simulation = point d'entrée (ou dernier close).
    e = float(entry) if entry is not None else closes[-1]
    ex, ey = hist_r, _y(e)
    draw.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=_DX_ENTRY)

    # Chemins de simulation (pointillés depuis l'entrée vers cible / invalidation).
    if target is not None:
        _dashed_line(draw, ex, ey, plot_r, _y(float(target)), _DX_TARGET, width=2)
    if invalidation is not None:
        _dashed_line(draw, ex, ey, plot_r, _y(float(invalidation)), _DX_INVAL, width=2)

    # Bulles d'entrée / de sortie (multiples si DCA / sorties échelonnées).
    # markers : liste de (kind, index, price[, label]) — kind = 'entry'|'exit'.
    # index négatif ou None -> placé au point d'entrée (départ de la simulation).
    n_e = n_s = 0
    for m in (markers or []):
        kind, idx, price, label = _unpack_marker(m)
        if price is None:
            continue
        if idx is None or idx < 0 or idx >= n:
            mx = hist_r
        else:
            mx = _xc(idx)
        my = _y(float(price))
        if kind == "exit":
            n_s += 1
            _bubble(draw, mx, my, _DX_TARGET, label or f"S{n_s}")
        else:
            n_e += 1
            _bubble(draw, mx, my, _DX_ENTRY, label or f"E{n_e}")

    # Étiquettes honnêtes.
    draw.text((plot_l + 2, 5), "OHLCV reel  ·  MA7", fill=_DX_TEXT)
    draw.text((hist_r + 4, 5), f"SIMULATION {horizon_weeks} sem. (scenario)", fill=_DX_TEXT)
    draw.text((plot_l + 2, vol_t - 1), "Vol", fill=_DX_TEXT)
    return _data_uri(img)


def _unpack_marker(m):
    """Accepte (kind, index, price[, label]) ou {kind,index,price,label}. -> tuple normalisé."""
    if isinstance(m, dict):
        return (
            str(m.get("kind") or "entry"), m.get("index"),
            m.get("price"), m.get("label"),
        )
    kind = m[0] if len(m) > 0 else "entry"
    idx = m[1] if len(m) > 1 else None
    price = m[2] if len(m) > 2 else None
    label = m[3] if len(m) > 3 else None
    return (str(kind), idx, price, label)


def _bubble(draw, x, y, color, label: str, *, r: int = 7) -> None:
    """Dessine une bulle pastille avec un contour + un petit label (E1, S2...)."""
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(13, 17, 23))
    # centre le label (approx : ~5 px/caractère avec la police par défaut).
    tx = x - int(len(label) * 2.5)
    draw.text((tx, y - 4), label, fill=(13, 17, 23))


def _fmt_axis(price: float) -> str:
    """Formatte un prix pour l'axe (compact, adapté aux micro-caps)."""
    ap = abs(price)
    if ap == 0:
        return "0"
    if ap >= 1:
        return f"{price:.2f}"
    if ap >= 0.01:
        return f"{price:.4f}"
    return f"{price:.2e}"


def _dashed_line(draw, x0, y0, x1, y1, color, *, dash=6, gap=4, width=1):
    """Trace une ligne pointillée quelconque (x0,y0)->(x1,y1)."""
    import math

    dx, dy = x1 - x0, y1 - y0
    dist = max(1.0, math.hypot(dx, dy))
    steps = int(dist // (dash + gap))
    ux, uy = dx / dist, dy / dist
    for k in range(steps + 1):
        sx = x0 + ux * k * (dash + gap)
        sy = y0 + uy * k * (dash + gap)
        ex_ = sx + ux * dash
        ey_ = sy + uy * dash
        draw.line([sx, sy, ex_, ey_], fill=color, width=width)


def save_png_data_uri(data_uri: str, path: str) -> str:
    """Décode un PNG data-URI et l'écrit sur disque (vrai screenshot). Retourne le chemin."""
    import base64 as _b64
    import os

    prefix = "data:image/png;base64,"
    payload = data_uri[len(prefix):] if data_uri.startswith(prefix) else data_uri
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_b64.b64decode(payload))
    return path
