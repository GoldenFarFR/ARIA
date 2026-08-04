"""Signaux d'entrée : Fibonacci golden pocket + divergence RSI (déterministe, offline)."""
from __future__ import annotations

import pytest

from aria_core.skills import entry_signals
from aria_core.skills.entry_signals import (
    RSI_DIVERGENCE_MAX,
    RSI_DIVERGENCE_MIN,
    RSI_EXIT_DIVERGENCE_MAX,
    RSI_EXIT_DIVERGENCE_MIN,
    bearish_rsi_divergence,
    bullish_rsi_divergence,
    detect_entry,
    fibonacci_zone,
    rsi_series,
)
from aria_core.skills.ta_levels import Candle


def _candles(closes: list[float]) -> list[Candle]:
    return [Candle(ts=i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


# ── RSI ──────────────────────────────────────────────────────────────────────

def test_rsi_rising_is_high():
    rsis = rsi_series([100 + i for i in range(20)])
    assert rsis[-1] is not None and rsis[-1] > 95  # que des gains -> RSI ~100


def test_rsi_falling_is_low():
    rsis = rsi_series([100 - i for i in range(20)])
    assert rsis[-1] is not None and rsis[-1] < 5


def test_rsi_warmup_is_none():
    rsis = rsi_series([100, 101, 102], period=14)
    assert all(r is None for r in rsis)  # trop court


# ── Fibonacci ────────────────────────────────────────────────────────────────

def test_fibonacci_zone_levels():
    fib = fibonacci_zone(_candles([100, 120, 140]))
    assert fib["high"] == 140 and fib["low"] == 100
    # 0.618 retracement = 140 - 40*0.618 = 115.28 ; 0.786 = 140 - 40*0.786 = 108.56
    assert abs(fib["gp_high"] - 115.28) < 0.1
    assert abs(fib["gp_low"] - 108.56) < 0.1


def test_fibonacci_flat_is_none():
    assert fibonacci_zone(_candles([100, 100, 100])) is None


# ── détecteur de jambe ZigZag/ATR (04/08, remplace le max/min sur fenêtre fixe) ──
# Contexte réel : un ordre ZRO en prod (scalping_v7) a déclenché "dans la golden
# pocket" à un prix qui ne respectait même pas le vrai Fibonacci du graphique --
# `fibonacci_zone()` mesurait juste le max/min des 25 dernières bougies passées
# par `detect_entry`, sans jamais détecter le vrai swing. Diagnostic vérifié
# (recalcul inverse) + confirmé par second avis Fable 5, qui a aussi trouvé deux
# aggravants (tolérance 3% disproportionnée sur une jambe étroite, aucun contrôle
# que la jambe est bien haussière) -- les deux corrigés ci-dessous.

from aria_core.skills.entry_signals import _zigzag_pivots, detect_swing_leg


def test_detect_swing_leg_none_too_short_series():
    """Pas assez de bougies pour que l'ATR chauffe -- jamais une jambe inventée."""
    assert detect_swing_leg(_candles([100, 101, 102])) is None


def test_detect_swing_leg_none_on_fresh_downswing():
    """04/08, spec Fable 5 explicite : si les deux derniers pivots confirmés sont
    haut PUIS bas (une jambe baissière déjà en cours), aucune jambe haussière
    n'est retournée -- jamais un repli silencieux sur l'ancien max/min, qui
    aurait mesuré cette baisse comme si c'était une "golden pocket" valide."""
    lead_in = [100.0] * 20
    capitulation = _ramp(100, 20, 8)
    bounce = _ramp(20, 300, 12)
    decline = _ramp(300, 20, 14)
    rebound = [60, 90]  # confirme le nouveau pivot bas (54) comme le PLUS RÉCENT
    closes = lead_in + capitulation + bounce + decline + rebound
    c = _candles(closes)
    assert detect_swing_leg(c, mode=None) is None
    assert detect_swing_leg(c, mode="scalping") is None


def test_detect_swing_leg_finds_real_swing_beyond_truncated_window():
    """Reproduit directement le mécanisme du bug ZRO réel (04/08) : le vrai creux
    (20) est loin en arrière dans la série (au-delà des 25 dernières bougies) --
    l'ANCIEN mécanisme (``fibonacci_zone`` sur une fenêtre tronquée à 25) rate
    complètement le vrai swing et mesure un bruit de plateau sans rapport ;
    ``detect_swing_leg`` cherche sur TOUTE la série fournie et retrouve le vrai
    creux quel que soit son ancienneté."""
    lead_in = [100.0] * 20
    capitulation = _ramp(100, 20, 8)
    bounce = _ramp(20, 300, 12)
    plateau = [280 + (i % 5) for i in range(30)]  # pousse le vrai creux hors des 25 dernières
    closes = lead_in + capitulation + bounce + plateau
    c = _candles(closes)

    old_window_fib = fibonacci_zone(c[-25:])  # ancien mécanisme, gardé pour comparaison
    assert old_window_fib["low"] == 280  # rate le vrai creux -- bruit de plateau
    assert old_window_fib["high"] != 300

    leg = detect_swing_leg(c)
    assert leg is not None
    assert leg["low"] == 20.0 and leg["high"] == 300.0  # le vrai swing, retrouvé


def test_rsi_divergence_lookback_stays_decoupled_from_swing_detection():
    """04/08, verrou de non-régression explicite (recommandation Fable 5,
    second avis) : ``lookback`` (25 par défaut) ne doit influencer QUE la
    recherche de pivots pour la divergence RSI -- jamais la détection de la
    jambe Fibonacci. Garantie STRUCTURELLE (pas seulement comportementale) :
    ``detect_swing_leg`` n'accepte même pas de paramètre ``lookback`` -- il ne
    peut donc pas être borné par erreur. La preuve comportementale (la jambe
    détectée voit bien au-delà d'une fenêtre de 25 bougies) est couverte par
    ``test_detect_swing_leg_finds_real_swing_beyond_truncated_window``
    ci-dessus."""
    import inspect

    from aria_core.skills.entry_signals import _DEFAULT_LOOKBACK

    assert _DEFAULT_LOOKBACK == 25  # valeur validée opérateur pour la divergence RSI -- ne pas dériver en silence
    assert "lookback" not in inspect.signature(detect_swing_leg).parameters


def test_zone_tolerance_proportional_narrower_than_old_fixed_on_tight_zone():
    """04/08, aggravant trouvé par Fable 5 : sur une jambe étroite (comme le
    ZRO réel, ~4-5% d'amplitude), l'ancienne tolérance fixe de 3% élargit la
    zone d'entrée quasi autant que la golden pocket elle-même -- la nouvelle
    tolérance proportionnelle (fraction de la largeur de zone, plafonnée à 3%)
    rejette un prix que l'ancienne tolérance fixe aurait accepté à tort."""
    lead_in = [100.0] * 15
    capitulation = [100, 90, 82, 77]
    bounce = [85, 93, 98, 101, 103]
    retest = [96, 88, 79, 75]
    tail = [81]  # entre gp_low*0.97 (ancienne tolérance) et gp_low (zone stricte)
    c = _candles(lead_in + capitulation + bounce + retest + tail)

    sig_proportional = detect_entry(c, lookback=25)
    assert sig_proportional.in_golden_pocket is False  # rejeté par la nouvelle tolérance

    sig_old_fixed = detect_entry(c, lookback=25, tolerance=0.03)
    assert sig_old_fixed.in_golden_pocket is True  # l'ancienne tolérance fixe l'aurait accepté


# ── le setup complet ─────────────────────────────────────────────────────────

def _setup_series() -> list[float]:
    """Divergence haussière classique : capitulation (creux 1, RSI au plancher),
    fort rebond, puis retest légèrement plus BAS (creux 2) mais RSI plus HAUT.

    Les 15 bougies d'amorce garantissent que le RSI est « chauffé » (période 14)
    AVANT le premier creux — sinon il serait ignoré (en prod on a 120+ bougies)."""
    lead_in = [100.0] * 15
    capitulation = [100, 90, 82, 77]   # creux 1 = 77 (chute franche -> RSI ~0)
    bounce = [85, 93, 98, 101, 103]    # fort rebond -> RSI remonte
    retest = [96, 88, 79, 75]          # creux 2 = 75 (plus bas) mais RSI plus haut
    # 04/08 -- recalibré pour la vraie jambe ZigZag/ATR (77 -> 103, confirmée
    # géométriquement, remplace l'ancienne fenêtre fixe de 25 bougies) : sa
    # golden pocket réelle est [82.56, 86.93], pas le [77, 103]*0.618-0.786
    # d'une fenêtre tronquée arbitraire -- 84 y retombe (petit rebond depuis
    # le creux 2), 80 (ancienne valeur) n'y était plus une fois la vraie
    # jambe mesurée correctement.
    tail = [84]                        # petit rebond, prix courant dans le vrai golden pocket
    return lead_in + capitulation + bounce + retest + tail


def test_bullish_divergence_detected():
    ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
    assert ok is True
    assert "RSI remonte" in base


def test_no_divergence_on_plain_downtrend():
    ok, _ = bullish_rsi_divergence(_candles([100 - i for i in range(30)]), lookback=25)
    assert ok is False


def _non_adjacent_divergence_series() -> list[float]:
    """19/07 -- reproduit le vrai cas trouvé en investiguant 8 candidats réels du
    pipeline momentum (0/8 divergence détectée, alors que 4/8 étaient dans le golden
    pocket) : 3 creux, où seule la paire NON-adjacente (le premier et le dernier)
    forme une vraie divergence -- la paire immédiate (2e et 3e creux) n'en forme
    aucune (RSI continue de baisser d'un creux à l'autre juste avant le rebond final).
    """
    lead_in = [100.0] * 15
    trough1 = [95, 92, 90]      # creux 1 = 90, RSI très bas (chauffe juste après lead_in)
    bounce1 = [96, 103, 108]
    trough2 = [104, 99, 95]     # creux 2 = 95, retracement léger, RSI encore assez haut
    bounce2 = [100, 105]
    trough3 = [98, 85, 70]      # creux 3 = 70, nouveau plus bas marqué
    tail = [75]                 # confirme le creux 3 comme pivot (minimum local)
    return lead_in + trough1 + bounce1 + trough2 + bounce2 + trough3 + tail


def test_bullish_divergence_detected_across_non_adjacent_pivots():
    """La paire de creux IMMÉDIATEMENT adjacente (2e, 3e) ne forme PAS de divergence
    ici (vérifié : RSI y baisse) -- seule la comparaison avec un creux plus ancien
    (1er, 3e) la révèle. L'ancien code (limité à pivots[-2]/pivots[-1]) aurait raté
    ce cas -- verrou de non-régression sur le correctif du 19/07."""
    ok, base = bullish_rsi_divergence(_candles(_non_adjacent_divergence_series()), lookback=25)
    assert ok is True
    assert "RSI remonte" in base
    assert "70" in base  # ancré sur le creux le plus récent (3e), pas un pivot intermédiaire


# ── plage RSI absolue [20, 40] au point de divergence (25/07) ────────────────
# Incident réel opérateur : un achat (ZEN) a été déclenché sur "RSI remonte
# (39 → 40)" -- une divergence purement relative (creux 2 < creux 1, RSI plus
# haut), mais 39/40 n'est pas une vraie zone de survente. `bullish_rsi_divergence`
# exige désormais que le RSI du creux RÉCENT (r2) tombe lui-même dans [20, 40],
# quel que soit le timeframe des bougies. Les tests ci-dessous surchargent
# ponctuellement `rsi_series` (même série de prix que `_setup_series()`, dont la
# divergence relative est déjà validée par `test_bullish_divergence_detected` --
# seule la valeur RSI au creux récent change) : calibrer une vraie série de prix
# pour produire un RSI pivot précis au-dessus de 40 s'est avéré non-trivial (le
# RSI Wilder plafonne naturellement autour de ~38 dès qu'un NOUVEAU plus bas de
# prix est requis) -- contrôler directement la valeur RSI teste la contrainte de
# plage elle-même, indépendamment de cette limite arithmétique du RSI.
_SETUP_RECENT_PIVOT_INDEX = 27  # indice du creux récent (75) dans _setup_series()


def _rsi_series_forcing_recent_pivot(closes: list[float], period: int = 14, *, value: float):
    """Vrai `rsi_series`, sauf au creux récent du setup où le RSI est forcé à `value`."""
    real = rsi_series(closes, period)
    real[_SETUP_RECENT_PIVOT_INDEX] = value
    return real


def test_bullish_divergence_rejected_when_recent_rsi_above_max(monkeypatch):
    """Divergence relative valide (creux 75 < 77, RSI qui remonte) mais RSI récent
    à 55 (> RSI_DIVERGENCE_MAX=40) -- doit être rejetée (reproduit l'incident ZEN,
    où 39→40 n'est pas une vraie zone de survente)."""
    monkeypatch.setattr(
        entry_signals, "rsi_series",
        lambda closes, period=14: _rsi_series_forcing_recent_pivot(closes, period, value=55.0),
    )
    ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
    assert ok is False
    assert base == ""


def test_bullish_divergence_rejected_when_recent_rsi_below_min(monkeypatch):
    """Divergence relative valide mais RSI récent à 10 (< RSI_DIVERGENCE_MIN=20) --
    doit être rejetée (survente trop extrême, hors de la zone de rebond visée)."""
    monkeypatch.setattr(
        entry_signals, "rsi_series",
        lambda closes, period=14: _rsi_series_forcing_recent_pivot(closes, period, value=10.0),
    )
    ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
    assert ok is False
    assert base == ""


def test_bullish_divergence_accepted_at_range_boundaries(monkeypatch):
    """Les bornes [20, 40] sont inclusives -- un RSI récent exactement à 20 ou 40
    reste une divergence valide (seul l'extérieur strict de la plage est rejeté)."""
    for boundary in (RSI_DIVERGENCE_MIN, RSI_DIVERGENCE_MAX):
        monkeypatch.setattr(
            entry_signals, "rsi_series",
            lambda closes, period=14, v=boundary: _rsi_series_forcing_recent_pivot(closes, period, value=v),
        )
        ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
        assert ok is True, f"borne {boundary} devrait être acceptée"
        assert "RSI remonte" in base


# ── _bullish_rsi_divergence_detail (Item #183, 28/07 -- gap/span de qualité) ──

def test_divergence_detail_exposes_gap_and_span_when_present():
    from aria_core.skills.entry_signals import _bullish_rsi_divergence_detail

    detail = _bullish_rsi_divergence_detail(_candles(_setup_series()), lookback=25)
    assert detail.present is True
    assert detail.gap is not None and detail.gap > 0  # RSI remonte -> gap positif
    assert detail.span is not None and detail.span > 0  # bougies entre les 2 pivots


def test_divergence_detail_none_when_absent():
    from aria_core.skills.entry_signals import _bullish_rsi_divergence_detail

    detail = _bullish_rsi_divergence_detail(_candles([100 + i for i in range(30)]), lookback=25)
    assert detail.present is False
    assert detail.gap is None and detail.span is None


def test_bullish_rsi_divergence_wrapper_unchanged_signature():
    """La fonction publique reste un tuple (bool, str) -- aucun appelant
    existant ne doit voir sa signature changer (Item #183)."""
    ok, base = bullish_rsi_divergence(_candles(_setup_series()), lookback=25)
    assert isinstance(ok, bool)
    assert isinstance(base, str)
    assert ok is True and "RSI remonte" in base


# ── bearish_rsi_divergence (Item #105, 26/07 -- signal de SORTIE scalping) ────

def _bearish_setup_series() -> list[float]:
    """Divergence baissière classique, miroir de _setup_series() : euphorie
    (sommet 1, RSI très haut), repli, nouveau sommet PLUS HAUT (sommet 2) mais
    RSI plus bas -- calibré empiriquement pour un RSI au pivot récent dans
    [60, 80] (69, ici)."""
    lead_in = [100.0] * 15
    euphoria = [100, 110, 118, 123]      # sommet 1 = 123, RSI très haut
    pullback = [115, 107, 102, 99, 97]   # repli
    retest = [104, 112, 121, 125]        # sommet 2 = 125 (plus haut) mais RSI plus bas
    tail = [120]                          # confirme le sommet 2 comme pivot
    return lead_in + euphoria + pullback + retest + tail


def test_bearish_divergence_detected():
    ok, base = bearish_rsi_divergence(_candles(_bearish_setup_series()), lookback=25)
    assert ok is True
    assert "RSI faiblit" in base


def test_no_bearish_divergence_on_plain_uptrend():
    ok, _ = bearish_rsi_divergence(_candles([100 + i for i in range(30)]), lookback=25)
    assert ok is False


_BEARISH_SETUP_RECENT_PIVOT_INDEX = 27  # sommet récent (125) dans _bearish_setup_series()


def _rsi_series_forcing_recent_bearish_pivot(closes: list[float], period: int = 14, *, value: float):
    real = rsi_series(closes, period)
    real[_BEARISH_SETUP_RECENT_PIVOT_INDEX] = value
    return real


def test_bearish_divergence_rejected_when_recent_rsi_above_max(monkeypatch):
    """Divergence relative valide (sommet 125 > 123, RSI qui faiblit) mais RSI
    récent à 90 (> RSI_EXIT_DIVERGENCE_MAX=80) -- doit être rejetée (pas une
    vraie zone de surachat affaiblissante)."""
    monkeypatch.setattr(
        entry_signals, "rsi_series",
        lambda closes, period=14: _rsi_series_forcing_recent_bearish_pivot(closes, period, value=90.0),
    )
    ok, base = bearish_rsi_divergence(_candles(_bearish_setup_series()), lookback=25)
    assert ok is False
    assert base == ""


def test_bearish_divergence_rejected_when_recent_rsi_below_min(monkeypatch):
    """RSI récent à 50 (< RSI_EXIT_DIVERGENCE_MIN=60) -- doit être rejetée
    (pas encore une vraie faiblesse de surachat)."""
    monkeypatch.setattr(
        entry_signals, "rsi_series",
        lambda closes, period=14: _rsi_series_forcing_recent_bearish_pivot(closes, period, value=50.0),
    )
    ok, base = bearish_rsi_divergence(_candles(_bearish_setup_series()), lookback=25)
    assert ok is False
    assert base == ""


def test_bearish_divergence_accepted_at_range_boundaries(monkeypatch):
    """Les bornes [60, 80] sont inclusives."""
    for boundary in (RSI_EXIT_DIVERGENCE_MIN, RSI_EXIT_DIVERGENCE_MAX):
        monkeypatch.setattr(
            entry_signals, "rsi_series",
            lambda closes, period=14, v=boundary: _rsi_series_forcing_recent_bearish_pivot(closes, period, value=v),
        )
        ok, base = bearish_rsi_divergence(_candles(_bearish_setup_series()), lookback=25)
        assert ok is True, f"borne {boundary} devrait être acceptée"
        assert "RSI faiblit" in base


def test_detect_entry_fires_on_setup():
    sig = detect_entry(_candles(_setup_series()), lookback=25)
    assert sig.present is True
    assert sig.in_golden_pocket and sig.rsi_divergence
    assert sig.entry is not None and sig.invalidation < sig.entry < sig.target
    assert sig.rr is not None and sig.rr > 1  # R/R favorable par construction


def test_detect_entry_exposes_golden_pocket_bounds():
    """Item #101 (26/07), demande operateur ("aria doit pouvoir connaitre en
    temps reel toute les valeurs de son golden pocket d'entree et de sortie") --
    gp_low/gp_high (les bornes 0,618/0,786 de la zone) doivent etre exposees,
    pas seulement invalidation/target derives."""
    sig = detect_entry(_candles(_setup_series()), lookback=25)
    assert sig.gp_low is not None and sig.gp_high is not None
    assert sig.gp_low < sig.gp_high
    # 30/07 -- invalidation = min(gp_low avec une marge de 2%, plancher ATR) : jamais
    # PLUS PRES de l'entree que la formule Fibonacci fixe (le plancher ATR ne peut que
    # l'ELOIGNER davantage si le token est plus volatil que 2%, jamais la resserrer).
    assert sig.invalidation <= sig.gp_low * 0.98 + 1e-9


def test_detect_entry_absent_on_uptrend():
    sig = detect_entry(_candles([100 + i for i in range(30)]), lookback=25)
    assert sig.present is False


def test_detect_entry_short_series_safe():
    sig = detect_entry(_candles([100, 101, 102]), lookback=25)
    assert sig.present is False


def _ramp(a: float, b: float, n: int) -> list[float]:
    """``n`` points strictly between ``a`` (exclusive) and ``b`` (inclusive) --
    a smooth, many-candle move (unlike the few-giant-jump fixtures elsewhere
    in this file) so ATR stays small relative to the swing's total amplitude,
    the realistic shape real OHLCV data has (04/08, needed to build fixtures
    where the ZigZag swing detector and a golden-pocket retracement don't
    fight each other -- see ``_confirmed_leg_series`` below)."""
    return [a + (b - a) * (i + 1) / n for i in range(n)]


def _confirmed_leg_series() -> list[float]:
    """A real ATR-confirmed low -> high leg (20 -> 300), with price only
    PARTWAY back down through the retracement -- not yet in the golden
    pocket, no divergence yet. 04/08, replaces the old monotonic-uptrend
    fixture for the "zone computable but setup not there yet" test below:
    a monotonic move has no confirmed reversal at all under the new
    ZigZag/ATR swing detector (see the dedicated fail-closed test), so it no
    longer represents a "not there yet" case -- this does."""
    lead_in = [100.0] * 20
    capitulation = _ramp(100, 20, 8)
    bounce = _ramp(20, 300, 12)
    decline = _ramp(300, 200, 6)  # repli partiel, encore loin de la zone [79.9, 127.0]
    return lead_in + capitulation + bounce + decline


def test_detect_entry_exposes_zone_bounds_even_absent_when_computable():
    """Item #182 (28/07), golden-pocket liberation: gp_low/gp_high/range_high/
    range_low are geometric facts derived from the detected swing leg,
    independent of whether RSI has confirmed a divergence -- a caller
    building a watch-and-wait limit order for a "not there yet" setup needs
    them even when present=False, as long as a real ATR-confirmed leg exists.

    04/08 -- recalibrated for the ZigZag/ATR swing detector (see
    ``detect_swing_leg``): the old fixture (a bare monotonic uptrend) no
    longer represents this case, since a one-directional move never confirms
    a reversal pivot at all (see
    ``test_detect_entry_absent_no_zone_on_unconfirmed_monotonic_move``
    below) -- ``_confirmed_leg_series`` provides a genuinely confirmed leg
    instead."""
    sig = detect_entry(_candles(_confirmed_leg_series()), lookback=25)
    assert sig.present is False
    assert sig.rsi_divergence is False
    assert sig.gp_low is not None and sig.gp_high is not None
    assert sig.gp_low < sig.gp_high
    assert sig.range_high is not None and sig.range_low is not None
    assert sig.range_low < sig.gp_low < sig.gp_high < sig.range_high


def test_detect_entry_absent_no_zone_on_unconfirmed_monotonic_move():
    """04/08, ZigZag/ATR swing detector, fail-closed spec (operator + Fable 5
    second opinion): a one-directional move NEVER confirms a reversal pivot
    (the ZigZag needs a real retracement of ATR x multiplier magnitude to
    confirm even the FIRST pivot) -- so ``detect_swing_leg`` returns None and
    no golden pocket is fabricated. This intentionally REVERSES the old
    (buggy) expectation, where a naive max/min over the window always
    produced *some* zone even on a move with no real structure -- exactly the
    geometry bug found live on ZRO (a leg measured on the wrong swing is
    worse than admitting no signal)."""
    sig = detect_entry(_candles([100 + i for i in range(30)]), lookback=25)
    assert sig.present is False
    assert sig.gp_low is None and sig.gp_high is None
    assert sig.range_high is None and sig.range_low is None


def test_detect_entry_zone_bounds_none_when_no_computable_zone():
    """Too short a series never reaches fibonacci_zone() at all -- gp_low/
    gp_high/range_high/range_low must stay None, never a fabricated level."""
    sig = detect_entry(_candles([100, 101, 102]), lookback=25)
    assert sig.gp_low is None and sig.gp_high is None
    assert sig.range_high is None and sig.range_low is None


def test_detect_entry_confirmed_setup_also_exposes_range_bounds():
    """range_high/range_low are populated on the confirmed (present=True)
    path too, consistent with the absent-but-computable path above."""
    sig = detect_entry(_candles(_setup_series()), lookback=25)
    assert sig.present is True
    assert sig.range_high is not None and sig.range_low is not None
    assert sig.range_low < sig.gp_low < sig.gp_high < sig.range_high
    assert sig.range_high == sig.target  # same value, same source (fib["high"])


def test_detect_entry_exposes_rsi_gap_and_span_when_confirmed():
    """Item #183 (28/07): rsi_gap/rsi_span populated on the confirmed setup,
    same values _bullish_rsi_divergence_detail computed internally."""
    sig = detect_entry(_candles(_setup_series()), lookback=25)
    assert sig.present is True
    assert sig.rsi_gap is not None and sig.rsi_gap > 0
    assert sig.rsi_span is not None and sig.rsi_span > 0


def test_detect_entry_rsi_gap_span_none_without_divergence():
    sig = detect_entry(_candles([100 + i for i in range(30)]), lookback=25)
    assert sig.rsi_divergence is False
    assert sig.rsi_gap is None and sig.rsi_span is None


# ── execution_price (19/07, trouvaille réelle en vérifiant la légitimité d'un trade
#    GITLAWB à la demande de l'opérateur) : le R/R doit refléter le prix RÉELLEMENT
#    exécutable (DexScreener temps réel), pas le close d'une AUTRE source (OHLCV) qui
#    peut diverger de plusieurs % au même instant nominal ─────────────────────────────

def test_execution_price_absent_keeps_close_as_entry():
    """Comportement INCHANGÉ sans ``execution_price`` -- tout appelant existant
    (ex. acp_onchain_scan.py/`/vc`, où il n'y a pas d'exécution imminente à un prix
    précis) garde exactement le comportement d'avant ce chantier."""
    candles = _candles(_setup_series())
    without = detect_entry(candles, lookback=25)
    with_none = detect_entry(candles, lookback=25, execution_price=None)
    assert without.entry == with_none.entry == candles[-1].close
    assert without.rr == with_none.rr


def test_execution_price_replaces_close_as_rr_reference():
    """Le R/R change selon la source de prix -- reproduit exactement le trade GITLAWB
    réel : un prix d'exécution plus ÉLOIGNÉ de l'invalidation (plus haut) réduit le R/R
    affiché par rapport au close utilisé en interne pour détecter le setup."""
    candles = _candles(_setup_series())
    close_based = detect_entry(candles, lookback=25)
    close = candles[-1].close
    higher_execution_price = close * 1.012  # +1.2% -- l'écart réel observé sur GITLAWB
    exec_based = detect_entry(candles, lookback=25, execution_price=higher_execution_price)

    assert exec_based.present is True
    assert exec_based.entry == higher_execution_price
    assert exec_based.entry != close_based.entry
    # target reste un niveau Fibonacci reel -- inchange, il decrit la STRUCTURE du
    # setup, pas un prix de remplissage.
    assert exec_based.target == close_based.target
    # 30/07 -- invalidation peut desormais differer legerement : le plancher ATR
    # (min(fib, entry*(1-atr_floor))) est ancre sur ENTRY, donc un execution_price
    # different peut deplacer LE PLANCHER (jamais le niveau Fibonacci lui-meme, qui
    # reste identique en interne) -- les deux restent neanmoins <= la formule fib pure.
    assert exec_based.invalidation <= exec_based.gp_low * 0.98 + 1e-9
    assert close_based.invalidation <= close_based.gp_low * 0.98 + 1e-9
    # Un prix d'entrée plus haut (plus proche de la cible, plus loin de l'invalidation
    # en absolu -- mais ici le déplacement du dénominateur domine) change le R/R --
    # jamais silencieusement ignoré.
    assert exec_based.rr != close_based.rr


def test_execution_price_inconsistent_with_invalidation_falls_back_to_close():
    """Un execution_price incohérent (<= invalidation -- donnée aberrante, jamais prise
    pour argent comptant) retombe sur le close, même garde que le chemin normal
    (``entry > invalidation``)."""
    candles = _candles(_setup_series())
    close_based = detect_entry(candles, lookback=25)
    # 30/07 -- largement sous gp_low (pas juste sous l'ancienne invalidation calculee) :
    # reste incoherent quel que soit le plancher ATR applique sur CE nouvel entry
    # (le plancher ne peut jamais faire remonter l'invalidation au-dessus de gp_low*0.98).
    absurd_price = close_based.gp_low * 0.5
    exec_based = detect_entry(candles, lookback=25, execution_price=absurd_price)

    assert exec_based.present is True
    assert exec_based.rr is None  # entry(absurd) <= invalidation -- garde existante, jamais un R/R inventé


def test_execution_price_zero_or_negative_ignored():
    """Une valeur non-physique (0 ou négative, ex. donnée manquante mal propagée) est
    ignorée -- retombe sur le close, jamais une division par un prix invalide."""
    candles = _candles(_setup_series())
    close_based = detect_entry(candles, lookback=25)
    for bad in (0.0, -1.0):
        exec_based = detect_entry(candles, lookback=25, execution_price=bad)
        assert exec_based.entry == close_based.entry
        assert exec_based.rr == close_based.rr


def test_execution_price_reproduces_gitlawb_real_trade_magnitude():
    """Reproduction directe du trade réel vérifié (19/07, demande opérateur) : signal
    proche de 149.1 sur le close, ~25.5 sur le prix RÉELLEMENT exécuté -- confirme que
    le mécanisme (deux sources de prix, jamais un bug de calcul) explique bien l'écart
    trouvé en conditions réelles, pas une supposition."""
    candles = _candles(_setup_series())
    signal_based = detect_entry(candles, lookback=25)
    close = candles[-1].close
    # Même ratio de divergence que le trade réel : exécution ~1.2% au-dessus du close.
    exec_based = detect_entry(candles, lookback=25, execution_price=close * 1.012)

    assert signal_based.rr > exec_based.rr  # le close (plus proche de l'invalidation) gonfle le R/R
    assert exec_based.rr > 1  # reste un R/R favorable, juste moins extrême


# ── plancher ATR d'invalidation, borne dédiée scalping (04/08) ────────────────

def test_invalidation_floor_pct_from_ratio_matches_manual_clamp():
    """Fonction pure (pas de candles) exposée spécifiquement pour qu'un script
    offline (recalcul rétroactif #8, diligence #9) importe la VRAIE formule au
    lieu de la réimplémenter -- verrouille son comportement exact contre toute
    dérive silencieuse future."""
    # swing : 2.5*0.10=0.25, dans les bornes [0.05,0.40] -- jamais clampé
    assert entry_signals._invalidation_floor_pct_from_ratio(0.10) == pytest.approx(0.25)
    # scalping : même ratio, bornes différentes [0.015,0.10] -- clampé au max
    assert entry_signals._invalidation_floor_pct_from_ratio(0.10, mode="scalping") == pytest.approx(0.10)
    # swing : ratio minuscule -- clampé au plancher 5%
    assert entry_signals._invalidation_floor_pct_from_ratio(0.001) == pytest.approx(entry_signals.MIN_ATR_INVALIDATION_PCT)
    # scalping : même ratio minuscule -- clampé à SON propre plancher, plus bas
    assert entry_signals._invalidation_floor_pct_from_ratio(0.001, mode="scalping") == pytest.approx(
        entry_signals.MIN_ATR_INVALIDATION_PCT_SCALPING
    )


def test_invalidation_floor_scalping_narrower_than_swing():
    """Bug réel trouvé en direct (diligence #9, 3 ordres v6/v7 tous pinnés à
    -5.0% pile) : le plancher ATR n'était jamais scopé par mode, comme
    l'ancien trailing stop avant sa correction du 03/08. Mêmes candles, deux
    modes -> deux planchers différents : le mode par défaut (swing) clampe
    vers le haut à 5% ; le mode scalping (bornes 1.5%-10%, calibrées sur des
    bougies 15-30min) laisse passer la vraie valeur ATR-dérivée sans la
    forcer artificiellement à un plancher taillé pour des bougies journalières."""
    base = 100.0
    step = 1.2  # ATR/close converge vers ~1.2% -> plancher brut 2.5*1.2% = 3%
    closes = [base if i % 2 == 0 else base + step for i in range(30)]
    candles = _candles(closes)

    swing_floor = entry_signals._invalidation_floor_pct(candles)
    scalping_floor = entry_signals._invalidation_floor_pct(candles, mode="scalping")

    assert swing_floor == pytest.approx(entry_signals.MIN_ATR_INVALIDATION_PCT)  # clampé à 5%
    assert scalping_floor < swing_floor  # jamais clampé au même plancher que le swing
    assert entry_signals.MIN_ATR_INVALIDATION_PCT_SCALPING <= scalping_floor <= entry_signals.MAX_ATR_INVALIDATION_PCT_SCALPING


def test_invalidation_floor_mode_none_matches_swing_default():
    """``mode=None`` (tout appelant existant non touché par ce changement) doit
    reproduire EXACTEMENT le comportement d'avant -- même valeur qu'un appel
    sans le paramètre ``mode`` du tout."""
    candles = _candles(_setup_series())
    assert entry_signals._invalidation_floor_pct(candles) == entry_signals._invalidation_floor_pct(candles, mode=None)
    assert entry_signals._invalidation_floor_pct(candles, mode="standard") == entry_signals._invalidation_floor_pct(candles, mode=None)


def _dual_mode_setup_series() -> list[float]:
    """04/08, ZigZag/ATR swing detector: a low->high leg large enough (20 ->
    300, over many small candles via ``_ramp``) that BOTH the swing
    (ATR x2.5) and scalping (ATR x1.5) multipliers confirm the exact same
    pivots -- the golden-pocket entry bounce itself must never be big enough
    to also register as a spurious new ZigZag pivot under EITHER threshold,
    which only holds when the swing develops gradually (small ATR relative
    to the total amplitude), unlike the few-giant-jump fixtures elsewhere in
    this file. Needed specifically because ``mode`` (04/08) now affects BOTH
    the swing-leg detection AND the invalidation floor -- this test isolates
    the floor difference by keeping the detected leg identical across modes."""
    lead_in = [100.0] * 20
    capitulation = _ramp(100, 20, 8)
    bounce = _ramp(20, 300, 12)
    decline = _ramp(300, 110, 10)  # repli vers la zone [79.9, 127.0], creux1 = 110
    rally = [122, 132]              # petit rebond -> RSI remonte
    creux2 = [108]                  # nouveau plus bas mais RSI plus haut (divergence)
    tail = [114]                    # prix courant, dans la zone
    return lead_in + capitulation + bounce + decline + rally + creux2 + tail


def test_detect_entry_scalping_mode_uses_narrower_invalidation_floor():
    """Bout-en-bout via ``detect_entry`` (pas juste la fonction interne) : le
    même setup golden-pocket+divergence produit une invalidation DIFFÉRENTE
    (donc un R/R différent) selon le mode -- preuve que le paramètre est bien
    câblé jusqu'au bout, pas seulement testé en isolation.

    04/08 -- fixture dédiée (``_dual_mode_setup_series``) : depuis que
    ``mode`` influence aussi la détection de la jambe elle-même (multiplicateur
    ATR du ZigZag), la fenêtre partagée doit garantir que les DEUX modes
    détectent la même jambe -- sinon gp_low/gp_high pourraient légitimement
    différer, ce qui ne testerait plus la même chose."""
    candles = _candles(_dual_mode_setup_series())
    swing_signal = detect_entry(candles, lookback=25)
    scalping_signal = detect_entry(candles, lookback=25, mode="scalping")

    assert swing_signal.present and scalping_signal.present
    assert swing_signal.gp_low == scalping_signal.gp_low  # même jambe détectée dans les deux modes
    assert swing_signal.gp_high == scalping_signal.gp_high
    # Le scalping ne peut jamais élargir l'invalidation au-delà de la structurelle --
    # seul un plancher PLUS ÉTROIT peut la rapprocher de l'entrée (jamais l'inverse).
    assert scalping_signal.invalidation >= swing_signal.invalidation
