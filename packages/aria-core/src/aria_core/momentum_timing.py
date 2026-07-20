"""Constantes de confirmation temporelle partagées par le pipeline momentum (20/07).

Extrait de ``paper_trader.HIGH_WATER_CONFIRMATION_SECONDS`` et
``momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS`` -- ces deux constantes
étaient des copies indépendantes de même valeur (75s), volontairement pas liées
par import direct pour éviter un cycle (``paper_trader.py`` importe déjà depuis
``momentum_entry.py``). Une revue croisée externe a signalé, à raison, que cette
duplication est une dette de maintenance réelle : rien n'empêche de changer l'une
sans penser à l'autre. Ce module neutre (aucune dépendance vers l'un ou l'autre)
est le SEUL point de vérité désormais -- les deux fichiers l'importent, jamais de
valeur recopiée à la main.
"""
from __future__ import annotations

MOMENTUM_CONFIRMATION_SECONDS = 75.0
