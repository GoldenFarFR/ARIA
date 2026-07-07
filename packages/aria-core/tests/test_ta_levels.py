"""Tests du moteur d'analyse technique déterministe (facts-only).

Toutes les séries OHLCV ici sont SYNTHÉTIQUES (extrema connus) — jamais
présentées comme un vrai token. On vérifie que les niveaux dérivent purement
des données et que les cas dégénérés (fenêtre vide / une bougie) ne lèvent pas.
"""
from __future__ import annotations

from aria_core.skills.ta_levels import (
    Candle,
    EntryZone,
    Level,
    TALevels,
    compute_levels,
    suggest_entry_zone,
)


def _candle(i: int, high: float, low: float, close: float) -> Candle:
    return Candle(ts=i, open=close, high=high, low=low, close=close, volume=100.0)


def _oscillating() -> list[Candle]:
    """Série synthétique oscillant entre plus-bas 100 et plus-haut 120.

    Le plus-haut 120 et le plus-bas 100 sont chacun testés plusieurs fois :
    résistance attendue à 120, support attendu à 100.
    """
    highs = [110, 120, 112, 120, 111, 120, 113, 119, 120, 112, 120, 110]
    lows = [100, 105, 100, 106, 100, 104, 100, 105, 100, 103, 100, 102]
    closes = [105, 118, 106, 117, 105, 116, 107, 115, 118, 106, 116, 104]
    return [_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]


def test_extrema_exact():
    candles = _oscillating()
    lv = compute_levels(candles)
    assert isinstance(lv, TALevels)
    # Plus-haut / plus-bas exacts, dérivés directement de la série.
    assert lv.plus_haut == 120.0
    assert lv.plus_bas == 100.0
    assert lv.dernier_close == 104.0
    assert lv.n_bougies == len(candles)


def test_support_resistance_derived_with_bases():
    lv = compute_levels(_oscillating())
    # Résistance à 120 et support à 100 présents.
    res = [r for r in lv.resistances if abs(r.prix - 120.0) < 1e-9]
    sup = [s for s in lv.supports if abs(s.prix - 100.0) < 1e-9]
    assert res, "résistance à 120 attendue"
    assert sup, "support à 100 attendu"
    # Niveaux testés plusieurs fois (touches ≥ 2).
    assert res[0].touches >= 2
    assert sup[0].touches >= 2
    assert res[0].type == "resistance"
    assert sup[0].type == "support"
    # Chaque niveau porte une base factuelle non vide, cohérente avec le prix.
    for level in lv.resistances + lv.supports:
        assert isinstance(level, Level)
        assert level.base
        assert "testé" in level.base
        assert "bougies" in level.base
    assert "120" in res[0].base
    assert "100" in sup[0].base


def test_trend_up():
    candles = [_candle(i, p + 1, p - 1, float(p)) for i, p in enumerate(range(100, 130))]
    lv = compute_levels(candles)
    assert lv.tendance == "haussière"
    assert "haussière" in lv.tendance_base


def test_trend_down():
    candles = [_candle(i, p + 1, p - 1, float(p)) for i, p in enumerate(range(130, 100, -1))]
    lv = compute_levels(candles)
    assert lv.tendance == "baissière"


def test_trend_flat_is_neutral():
    candles = [_candle(i, 101, 99, 100.0) for i in range(12)]
    lv = compute_levels(candles)
    assert lv.tendance == "neutre"


def test_empty_window():
    lv = compute_levels([])
    assert lv.plus_haut is None
    assert lv.plus_bas is None
    assert lv.dernier_close is None
    assert lv.supports == []
    assert lv.resistances == []
    assert lv.tendance == "indéterminée"
    assert lv.n_bougies == 0
    assert lv.bases  # une base explicative existe, pas d'exception


def test_single_candle():
    lv = compute_levels([_candle(0, 12.0, 9.0, 11.0)])
    assert lv.plus_haut == 12.0
    assert lv.plus_bas == 9.0
    assert lv.dernier_close == 11.0
    assert lv.n_bougies == 1
    # Une bougie : extrêmes présents comme niveaux, tendance indéterminée.
    assert any(abs(r.prix - 12.0) < 1e-9 for r in lv.resistances)
    assert any(abs(s.prix - 9.0) < 1e-9 for s in lv.supports)
    assert lv.tendance == "indéterminée"


def test_suggest_entry_zone_derived_ordering():
    lv = compute_levels(_oscillating())
    ez = suggest_entry_zone(lv)
    assert isinstance(ez, EntryZone)
    # Ordre cohérent : invalidation sous l'entrée, cible au-dessus.
    assert ez.invalidation < ez.entree
    assert ez.cible > ez.entree
    # Zone bornée par les extrêmes réels de la fenêtre.
    assert ez.cible <= lv.plus_haut + 1e-9
    assert ez.invalidation < lv.plus_bas + 1e-9 or ez.entree >= lv.plus_bas
    # La base explicite que la zone est dérivée des niveaux réels.
    assert "dérivée des niveaux réels" in ez.base


def test_suggest_entry_zone_none_on_empty():
    assert suggest_entry_zone(compute_levels([])) is None


def test_deterministic():
    candles = _oscillating()
    a = compute_levels(candles)
    b = compute_levels(candles)
    assert a == b
