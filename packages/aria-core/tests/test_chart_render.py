"""Tests du rendu graphique email-safe (PNG en data-URI, hors-ligne).

Séries OHLCV SYNTHÉTIQUES uniquement. On vérifie le contrat de sortie :
data-URI PNG valide (entête ``\\x89PNG``), dimensions demandées, gestion du vide.
"""
from __future__ import annotations

import base64
import io

from PIL import Image

from aria_core.skills.chart_render import render_price_chart_png
from aria_core.skills.ta_levels import Candle

_PREFIX = "data:image/png;base64,"


def _candles() -> list[Candle]:
    closes = [100, 104, 102, 108, 106, 112, 109, 115, 113, 118, 116, 120]
    return [
        Candle(ts=i, open=c, high=c + 3, low=c - 3, close=float(c), volume=50.0)
        for i, c in enumerate(closes)
    ]


def _decode(uri: str) -> bytes:
    assert uri.startswith(_PREFIX)
    return base64.b64decode(uri.split(",", 1)[1])


def test_returns_valid_png_datauri():
    uri = render_price_chart_png(_candles())
    raw = _decode(uri)
    # Entête PNG binaire.
    assert raw[:4] == b"\x89PNG"


def test_default_dimensions():
    uri = render_price_chart_png(_candles())
    img = Image.open(io.BytesIO(_decode(uri)))
    assert img.size == (560, 220)
    assert img.format == "PNG"


def test_custom_dimensions():
    uri = render_price_chart_png(_candles(), width=400, height=160)
    img = Image.open(io.BytesIO(_decode(uri)))
    assert img.size == (400, 160)


def test_with_levels_renders():
    uri = render_price_chart_png(
        _candles(), entry=104.0, invalidation=98.0, target=122.0
    )
    raw = _decode(uri)
    assert raw[:4] == b"\x89PNG"
    img = Image.open(io.BytesIO(raw))
    assert img.size == (560, 220)


def test_levels_change_output():
    # Les repères de niveaux modifient bien le rendu (déterministe mais distinct).
    plain = render_price_chart_png(_candles())
    with_levels = render_price_chart_png(
        _candles(), entry=104.0, invalidation=98.0, target=122.0
    )
    assert plain != with_levels


def test_deterministic():
    a = render_price_chart_png(_candles(), entry=104.0, target=122.0)
    b = render_price_chart_png(_candles(), entry=104.0, target=122.0)
    assert a == b


def test_empty_candles_handled():
    uri = render_price_chart_png([])
    # Géré sans exception : PNG neutre valide aux dimensions demandées.
    raw = _decode(uri)
    assert raw[:4] == b"\x89PNG"
    img = Image.open(io.BytesIO(raw))
    assert img.size == (560, 220)


def test_single_candle_handled():
    uri = render_price_chart_png([Candle(0, 10.0, 12.0, 9.0, 11.0, 10.0)])
    raw = _decode(uri)
    assert raw[:4] == b"\x89PNG"
