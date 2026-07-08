"""Tests du câblage Analyse technique -> rapport (data-gated, facts-only).

Vérifie que :
- ``_attach_ta`` reporte les niveaux réels + rend le graphique dans le VCResult
  UNIQUEMENT si une série OHLCV a été dérivée (sinon no-op strict) ;
- ``_ta_block_html`` rend la section (avec image PNG data-URI) quand la donnée
  existe, et renvoie une chaîne vide sinon (aucune section fantôme).

Tout est hors-ligne : les bougies sont synthétiques, le graphique est produit
par ``chart_render`` (Pillow, déterministe, sans réseau).
"""

from aria_core.skills.acp_onchain_scan import TokenScanContext
from aria_core.skills.ta_levels import Candle, compute_levels, suggest_entry_zone
from aria_core.skills.vc_analysis import VCResult, _attach_ta
from aria_core.skills.vc_i18n import report_strings
from aria_core.skills.vc_report import _ta_block_html

_S = report_strings("fr")


def _result() -> VCResult:
    return VCResult(
        contract="0x" + "a" * 40,
        potentiel=6,
        risque="ÉLEVÉ",
        these="thèse",
        recommandation="WATCH",
        taille_pct=0,
        entree="marché",
        invalidation="x",
        cible="y",
    )


def _oscillating(n: int = 60) -> list[Candle]:
    """Série qui monte et redescend : produit des supports/résistances nets."""
    candles = []
    for i in range(n):
        # zig-zag autour d'une pente douce
        wave = 5 * ((i % 10) - 5)
        base = 100.0 + i * 0.5 + wave
        candles.append(Candle(ts=1000 + i, open=base, high=base + 3, low=base - 3, close=base + 1))
    return candles


def _ctx_with_ta() -> TokenScanContext:
    candles = _oscillating()
    ta = compute_levels(candles)
    ctx = TokenScanContext(contract="0x" + "a" * 40, valid_address=True)
    ctx.ta = ta
    ctx.ta_entry = suggest_entry_zone(ta)
    ctx.ta_candles = candles
    ctx.ta_timeframe = "1D"
    return ctx


def test_attach_ta_noop_without_data():
    """Sans TA sur le ctx, le VCResult reste vierge (comportement inchangé)."""
    result = _result()
    ctx = TokenScanContext(contract="0x" + "a" * 40, valid_address=True)
    _attach_ta(result, ctx)
    assert result.ta_levels_lines == []
    assert result.chart_data_uri == ""
    assert result.ta_trend == ""


def test_attach_ta_populates_result_and_chart():
    result = _result()
    ctx = _ctx_with_ta()
    _attach_ta(result, ctx)
    assert result.ta_timeframe == "1D"
    assert result.ta_trend  # une tendance déterministe a été calculée
    assert result.ta_levels_lines  # au moins la ligne plus-haut/plus-bas
    # Graphique : PNG data-URI email-safe (jamais un lien externe).
    assert result.chart_data_uri.startswith("data:image/png;base64,")


def test_ta_block_html_omitted_when_empty():
    """Aucune section fantôme : sans donnée TA, le bloc est une chaîne vide."""
    assert _ta_block_html(_result(), _S) == ""


def test_ta_block_html_renders_levels_and_image():
    result = _result()
    _attach_ta(result, _ctx_with_ta())
    html = _ta_block_html(result, _S)
    assert "Analyse technique" in html
    assert "<img" in html and "data:image/png;base64," in html
    # facts-only : la mention « niveaux dérivés » figure, pas de promesse.
    assert "jamais fabriqu" in html
